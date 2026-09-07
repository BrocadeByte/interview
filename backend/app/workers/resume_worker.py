import asyncio
import json
import logging
import time
from enum import Enum

import aio_pika
from aiormq.exceptions import AMQPConnectionError
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401  导入模型以注册表元数据，保留未使用导入的检查豁免。
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.resume import Resume
from app.services.resume_queue import (
    declare_resume_topology,
    parse_resume_message,
    publish_resume_parse_dead_letter,
    publish_resume_parse_retry,
)
from app.services.resume_service import (
    ResumeParseTimeoutError,
    get_resume_parse_model_name,
    parse_resume_text,
    safe_parse_error,
    serialize_resume_parse_output,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

RESUME_PARSE_MAX_ATTEMPTS = 2
RETRY_PUBLISH_ERROR = "简历解析重试任务提交失败，请稍后重新上传简历"


class ResumeTaskResult(str, Enum):
    IGNORED = "ignored"
    PARSED = "parsed"
    RETRY = "retry"
    FAILED = "failed"


def is_transient_resume_error(exc: Exception) -> bool:
    """区分可重试的网络或数据库异常与不可重试的格式、校验及整体解析超时错误。"""
    if isinstance(exc, (json.JSONDecodeError, ValidationError, HTTPException)):
        return False
    if isinstance(exc, ResumeParseTimeoutError):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    if isinstance(exc, (OperationalError, DBAPIError)):
        return True
    transient_names = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailableError",
    }
    if type(exc).__name__ in transient_names:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("connection error", "connection reset", "rate limit", "temporarily unavailable")
    )


async def process_resume_task(
    db: AsyncSession,
    *,
    resume_id: int,
    user_id: int,
    attempt: int,
) -> ResumeTaskResult:
    """持有行锁时解析用户的待处理简历，防止重复投递导致重复处理。"""
    try:
        resume = await db.scalar(
            select(Resume)
            .where(Resume.id == resume_id, Resume.user_id == user_id)
            .with_for_update()
        )
        if resume is None:
            logger.info(
                "resume.worker.ignored resume_id=%s user_id=%s attempt=%s reason=not_found_or_owner_mismatch",
                resume_id,
                user_id,
                attempt,
            )
            await db.rollback()
            return ResumeTaskResult.IGNORED
        if resume.status == "parsed":
            logger.info(
                "resume.worker.ignored resume_id=%s user_id=%s attempt=%s reason=already_parsed",
                resume_id,
                user_id,
                attempt,
            )
            await db.rollback()
            return ResumeTaskResult.IGNORED
        if resume.status != "pending":
            logger.info(
                "resume.worker.ignored resume_id=%s user_id=%s attempt=%s reason=status_%s",
                resume_id,
                user_id,
                attempt,
                resume.status,
            )
            await db.rollback()
            return ResumeTaskResult.IGNORED

        llm_started = time.perf_counter()
        try:
            output = await parse_resume_text(resume.raw_text)
            parsed_json, profile_patch_json = serialize_resume_parse_output(output)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - llm_started) * 1000
            logger.warning(
                "resume.llm_parse.failed resume_id=%s user_id=%s attempt=%s model=%s "
                "elapsed_ms=%.1f success=false error_type=%s",
                resume_id,
                user_id,
                attempt,
                get_resume_parse_model_name(),
                elapsed_ms,
                type(exc).__name__,
            )
            await db.rollback()
            if attempt < RESUME_PARSE_MAX_ATTEMPTS and is_transient_resume_error(exc):
                return ResumeTaskResult.RETRY
            await mark_resume_failed(
                db,
                resume_id=resume_id,
                user_id=user_id,
                error_message=safe_parse_error(exc),
            )
            logger.warning(
                "resume.worker.failed resume_id=%s user_id=%s attempt=%s error_type=%s",
                resume_id,
                user_id,
                attempt,
                type(exc).__name__,
            )
            return ResumeTaskResult.FAILED

        resume.parsed_json = parsed_json
        resume.profile_patch_json = profile_patch_json
        resume.status = "parsed"
        resume.error_message = None
        await db.commit()
        elapsed_ms = (time.perf_counter() - llm_started) * 1000
        logger.info(
            "resume.llm_parse.completed resume_id=%s user_id=%s attempt=%s model=%s "
            "elapsed_ms=%.1f success=true",
            resume_id,
            user_id,
            attempt,
            get_resume_parse_model_name(),
            elapsed_ms,
        )
        logger.info(
            "resume.worker.completed resume_id=%s user_id=%s attempt=%s status=parsed",
            resume_id,
            user_id,
            attempt,
        )
        return ResumeTaskResult.PARSED
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "resume.worker.failed resume_id=%s user_id=%s attempt=%s error_type=%s",
            resume_id,
            user_id,
            attempt,
            type(exc).__name__,
        )
        if attempt < RESUME_PARSE_MAX_ATTEMPTS and is_transient_resume_error(exc):
            return ResumeTaskResult.RETRY
        await mark_resume_failed(
            db,
            resume_id=resume_id,
            user_id=user_id,
            error_message=safe_parse_error(exc),
        )
        return ResumeTaskResult.FAILED


async def mark_resume_failed(
    db: AsyncSession,
    *,
    resume_id: int,
    user_id: int,
    error_message: str,
) -> bool:
    """回滚后仅将指定用户仍待处理的简历标记失败，清除解析结果并提交。"""
    await db.rollback()
    result = await db.execute(
        update(Resume)
        .where(
            Resume.id == resume_id,
            Resume.user_id == user_id,
            Resume.status == "pending",
        )
        .values(
            status="failed",
            parsed_json=None,
            profile_patch_json=None,
            error_message=error_message,
        )
    )
    await db.commit()
    return bool(result.rowcount)


async def handle_resume_task(
    *,
    resume_id: int,
    user_id: int,
    attempt: int,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> ResumeTaskResult:
    """处理一次简历任务并发布重试或死信消息，重试投递失败时记录失败状态。"""
    async with session_factory() as db:
        result = await process_resume_task(
            db,
            resume_id=resume_id,
            user_id=user_id,
            attempt=attempt,
        )

    if result == ResumeTaskResult.RETRY:
        next_attempt = attempt + 1
        try:
            await publish_resume_parse_retry(resume_id, user_id, next_attempt)
        except Exception as exc:
            async with session_factory() as db:
                await mark_resume_failed(
                    db,
                    resume_id=resume_id,
                    user_id=user_id,
                    error_message=RETRY_PUBLISH_ERROR,
                )
            logger.error(
                "resume.queue.publish.failed resume_id=%s user_id=%s attempt=%s route=retry error_type=%s",
                resume_id,
                user_id,
                next_attempt,
                type(exc).__name__,
            )
            raise
        logger.warning(
            "resume.worker.retry resume_id=%s user_id=%s attempt=%s",
            resume_id,
            user_id,
            next_attempt,
        )
    elif result == ResumeTaskResult.FAILED:
        await publish_resume_parse_dead_letter(resume_id, user_id, attempt)
    return result


async def run_worker() -> None:
    """持续消费简历消息，校验消息结构并调度处理，记录无效消息及消费异常。"""
    connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        timeout=settings.rabbitmq_connect_timeout_seconds,
    )
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=settings.rabbitmq_prefetch_count)
        topology = await declare_resume_topology(channel)
        async with topology["work_queue"].iterator() as queue_iter:
            async for message in queue_iter:
                try:
                    async with message.process(requeue=False):
                        try:
                            task = parse_resume_message(message.body)
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                            logger.warning(
                                "resume.worker.invalid_message error_type=%s",
                                type(exc).__name__,
                            )
                            continue
                        await handle_resume_task(
                            resume_id=task.resume_id,
                            user_id=task.user_id,
                            attempt=task.attempt,
                        )
                except Exception as exc:
                    logger.error(
                        "resume.worker.message_rejected error_type=%s",
                        type(exc).__name__,
                    )
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except (AMQPConnectionError, TimeoutError, OSError) as exc:
        logger.error(
            "resume.worker.rabbitmq_unavailable error_type=%s",
            type(exc).__name__,
        )
        raise SystemExit(2) from exc
