import json
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message

from app.core.config import settings


RESUME_PARSE_ROUTING_KEY = "parse"
RESUME_DEAD_ROUTING_KEY = "dead"
RESUME_RETRY_DELAY_MS = 5_000


@dataclass(frozen=True)
class ResumeParseMessage:
    resume_id: int
    user_id: int
    attempt: int


async def declare_resume_topology(channel: aio_pika.abc.AbstractChannel) -> dict[str, Any]:
    """声明持久化的简历工作队列、延迟重试队列和死信路由。"""
    dead_exchange = await channel.declare_exchange(
        settings.rabbitmq_resume_dlx,
        ExchangeType.DIRECT,
        durable=True,
    )
    main_exchange = await channel.declare_exchange(
        settings.rabbitmq_resume_exchange,
        ExchangeType.DIRECT,
        durable=True,
    )
    dead_queue = await channel.declare_queue(
        f"{settings.rabbitmq_resume_queue}.dead",
        durable=True,
    )
    await dead_queue.bind(dead_exchange, routing_key=RESUME_DEAD_ROUTING_KEY)
    retry_queue = await channel.declare_queue(
        f"{settings.rabbitmq_resume_queue}.retry",
        durable=True,
        arguments={
            "x-message-ttl": RESUME_RETRY_DELAY_MS,
            "x-dead-letter-exchange": settings.rabbitmq_resume_exchange,
            "x-dead-letter-routing-key": RESUME_PARSE_ROUTING_KEY,
        },
    )
    work_queue = await channel.declare_queue(
        settings.rabbitmq_resume_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": settings.rabbitmq_resume_dlx,
            "x-dead-letter-routing-key": RESUME_DEAD_ROUTING_KEY,
        },
    )
    await work_queue.bind(main_exchange, routing_key=RESUME_PARSE_ROUTING_KEY)
    return {
        "exchange": main_exchange,
        "work_queue": work_queue,
        "retry_queue": retry_queue,
        "dead_queue": dead_queue,
    }


def _resume_message(resume_id: int, user_id: int, attempt: int) -> Message:
    """将简历、用户及尝试次数封装为持久化 JSON 消息，设置本次尝试的消息标识。"""
    payload = {"resume_id": resume_id, "user_id": user_id, "attempt": attempt}
    return Message(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        content_type="application/json",
        delivery_mode=DeliveryMode.PERSISTENT,
        message_id=f"resume:{resume_id}:attempt:{attempt}",
    )


def parse_resume_message(body: bytes) -> ResumeParseMessage:
    """解析简历任务消息，严格校验字段集合及各字段必须为正整数。"""
    payload = json.loads(body.decode("utf-8"))
    expected_keys = {"resume_id", "user_id", "attempt"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("Resume task message has an invalid shape")
    if any(isinstance(payload[key], bool) or not isinstance(payload[key], int) for key in expected_keys):
        raise ValueError("Resume task message fields must be integers")
    if payload["resume_id"] <= 0 or payload["user_id"] <= 0 or payload["attempt"] <= 0:
        raise ValueError("Resume task message fields must be positive")
    return ResumeParseMessage(**payload)


async def _publish(resume_id: int, user_id: int, attempt: int, route: str) -> None:
    """通过带发布确认的连接将简历任务发送到指定路由，并关闭连接。"""
    connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        timeout=settings.rabbitmq_connect_timeout_seconds,
    )
    try:
        channel = await connection.channel(publisher_confirms=True)
        topology = await declare_resume_topology(channel)
        message = _resume_message(resume_id, user_id, attempt)
        if route == "retry":
            await topology["retry_queue"].channel.default_exchange.publish(
                message,
                routing_key=topology["retry_queue"].name,
            )
        elif route == "dead":
            await topology["dead_queue"].channel.default_exchange.publish(
                message,
                routing_key=topology["dead_queue"].name,
            )
        else:
            await topology["exchange"].publish(message, routing_key=RESUME_PARSE_ROUTING_KEY)
    finally:
        await connection.close()


async def publish_resume_parse_task(resume_id: int, user_id: int, attempt: int = 1) -> None:
    """向工作路由发布简历解析任务，默认从第一次尝试开始。"""
    await _publish(resume_id, user_id, attempt, "parse")


async def publish_resume_parse_retry(resume_id: int, user_id: int, attempt: int) -> None:
    """将下一次简历解析尝试发送到延迟重试队列。"""
    await _publish(resume_id, user_id, attempt, "retry")


async def publish_resume_parse_dead_letter(resume_id: int, user_id: int, attempt: int) -> None:
    """将无法继续解析的简历任务发送到死信队列。"""
    await _publish(resume_id, user_id, attempt, "dead")
