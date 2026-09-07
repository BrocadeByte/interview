import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar, Token
from types import SimpleNamespace
from typing import Any


StreamDeltaCallback = Callable[[str], Awaitable[None]]
StreamTextDoneCallback = Callable[[str], Awaitable[None]]
MAX_STREAMED_FIELD_CHARS = 2000

_stream_delta_callback: ContextVar[StreamDeltaCallback | None] = ContextVar(
    "stream_delta_callback",
    default=None,
)
_stream_text_done_callback: ContextVar[StreamTextDoneCallback | None] = ContextVar(
    "stream_text_done_callback",
    default=None,
)


def set_stream_delta_callback(callback: StreamDeltaCallback) -> Token:
    """设置当前上下文的文本增量回调，并返回用于恢复原回调的令牌。"""
    return _stream_delta_callback.set(callback)


def reset_stream_delta_callback(token: Token) -> None:
    """使用上下文令牌恢复先前的文本增量回调。"""
    _stream_delta_callback.reset(token)


def set_stream_text_done_callback(callback: StreamTextDoneCallback) -> Token:
    """设置当前上下文的文本完成回调，并返回恢复令牌。"""
    return _stream_text_done_callback.set(callback)


def reset_stream_text_done_callback(token: Token) -> None:
    """使用上下文令牌恢复先前的文本完成回调。"""
    _stream_text_done_callback.reset(token)


async def invoke_json_with_streaming_field(
    llm: Any,
    messages: Sequence[Any],
    *,
    field: str,
    stream_field: bool = True,
) -> Any:
    """收集模型流并按需发布指定 JSON 字符串字段的临时文本；其余 JSON 与评分字段保留在缓冲区，临时增量须在结构和业务校验及数据库提交后与已提交文本核对。"""
    callback = _stream_delta_callback.get()
    if callback is None:
        return await llm.ainvoke(messages)

    chunks: list[str] = []
    published = ""
    async for chunk in llm.astream(messages):
        text = _content_to_text(chunk.content)
        if text:
            chunks.append(text)
        if not stream_field:
            continue

        extracted = extract_json_string_field("".join(chunks), field)[:MAX_STREAMED_FIELD_CHARS]
        if not extracted.startswith(published):
            # 正常追加式模型输出应让字段单调增长；供应商行为异常时停止草稿推送，
            # 最终由提交后的 text_done 安全校正。
            stream_field = False
            continue
        delta = extracted[len(published) :]
        if delta:
            await callback(delta)
            published = extracted
    return SimpleNamespace(content="".join(chunks))


async def publish_committed_text(
    text: str,
    *,
    chunk_chars: int = 3,
    chunk_delay_seconds: float = 0.04,
) -> None:
    """仅在业务事务提交后，流式发布已确定的正式文本。"""
    callback = _stream_delta_callback.get()
    done_callback = _stream_text_done_callback.get()
    if callback is None and done_callback is None:
        return
    authoritative = str(text or "").strip()
    if not authoritative:
        raise ValueError("Committed stream text must be non-empty")
    if callback is not None:
        size = max(1, chunk_chars)
        starts = range(0, len(authoritative), size)
        for start in starts:
            await callback(authoritative[start : start + size])
            if start + size < len(authoritative):
                # 短暂让出执行时间，使浏览器能够逐步渲染收到的文本增量。
                await asyncio.sleep(max(0, chunk_delay_seconds))
    if done_callback is not None:
        await done_callback(authoritative)


def extract_json_string_field(raw: str, field: str) -> str:
    """提取可能尚未接收完整的 JSON 字符串字段，供诊断和测试使用。"""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', raw)
    if not match:
        return ""

    result: list[str] = []
    index = match.end()
    while index < len(raw):
        char = raw[index]
        if char == '"':
            break
        if char != "\\":
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(raw):
            break
        escape = raw[index + 1]
        if escape == "u":
            digits = raw[index + 2 : index + 6]
            if len(digits) < 4 or not all(value in "0123456789abcdefABCDEF" for value in digits):
                break
            result.append(chr(int(digits, 16)))
            index += 6
            continue
        result.append(json.loads(f'"\\{escape}"'))
        index += 2
    return "".join(result)


def is_json_string_field_complete(raw: str, field: str) -> bool:
    """查找指定 JSON 字符串字段的未转义闭合引号，判断文本是否已接收完整。"""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', raw)
    if not match:
        return False
    escaped = False
    for char in raw[match.end() :]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return True
    return False


def _content_to_text(content: Any) -> str:
    """将字符串、模型内容块列表或其他值转换为可拼接的文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")
