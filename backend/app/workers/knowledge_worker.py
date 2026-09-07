import asyncio
import json
import logging

import aio_pika
from aiormq.exceptions import AMQPConnectionError
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeIngestionTask
from app.services.knowledge_ingestion_service import process_ingestion_task
from app.services.knowledge_queue import (
    declare_ingestion_topology,
    publish_ingestion_dead_letter,
    publish_ingestion_retry,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


async def run_worker() -> None:
    """消费知识入库消息并在独立会话中执行任务，按失败状态转入重试或死信队列。"""
    connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        timeout=settings.rabbitmq_connect_timeout_seconds,
    )
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=settings.rabbitmq_prefetch_count)
        topology = await declare_ingestion_topology(channel)
        async with topology["work_queue"].iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process(requeue=False):
                    task_id = int(json.loads(message.body.decode("utf-8"))["task_id"])
                    try:
                        async with SessionLocal() as db:
                            await process_ingestion_task(db, task_id)
                    except Exception:
                        async with SessionLocal() as db:
                            task = await db.scalar(
                                select(KnowledgeIngestionTask).where(KnowledgeIngestionTask.id == task_id)
                            )
                        if task and task.status == "failed":
                            await publish_ingestion_retry(task_id)
                            logger.warning("knowledge.ingestion.retry task_id=%s attempt=%s", task_id, task.attempts)
                        elif task and task.status == "dead":
                            await publish_ingestion_dead_letter(task_id)
                            logger.error("knowledge.ingestion.dead task_id=%s", task_id)
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except (AMQPConnectionError, TimeoutError, OSError) as exc:
        logger.error("knowledge.worker.rabbitmq_unavailable url=%s error=%s", settings.rabbitmq_url, exc)
        raise SystemExit(2) from exc
