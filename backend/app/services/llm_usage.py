import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LlmObservation:
    """描述一次模型调用的非敏感业务维度。"""

    operation: str
    session_id: int | None = None
    question_index: int | None = None
    json_repair_attempt: int | None = None


class ObservedLLM:
    """在不改变 LangChain 模型接口的前提下记录逐请求 usage。"""

    def __init__(self, model: Any, observation: LlmObservation) -> None:
        """保存被包装模型与本次调用的非敏感观测维度。"""
        self._model = model
        self._observation = observation

    def bind(self, **kwargs: Any) -> "ObservedLLM":
        """向底层模型传递绑定参数，并在新对象上保留观测上下文。"""
        bind = getattr(self._model, "bind", None)
        return ObservedLLM(bind(**kwargs) if callable(bind) else self._model, self._observation)

    async def ainvoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        """执行非流式模型调用，并在成功或异常时记录用量。"""
        started = time.perf_counter()
        try:
            response = await self._model.ainvoke(messages, *args, **kwargs)
        except Exception as exc:
            _log_usage(
                self._observation,
                response=None,
                model=self._model,
                streaming=False,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=exc,
            )
            raise
        _log_usage(
            self._observation,
            response=response,
            model=self._model,
            streaming=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return response

    async def astream(self, messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """透传流式分块，并从最终用量分块记录模型用量。"""
        started = time.perf_counter()
        usage_response: Any = None
        try:
            async for chunk in self._model.astream(messages, *args, **kwargs):
                if _has_usage(chunk):
                    usage_response = chunk
                yield chunk
        except Exception as exc:
            _log_usage(
                self._observation,
                response=usage_response,
                model=self._model,
                streaming=True,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=exc,
            )
            raise
        _log_usage(
            self._observation,
            response=usage_response,
            model=self._model,
            streaming=True,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )


def observe_llm(
    model: Any,
    *,
    operation: str,
    session_id: int | None = None,
    question_index: int | None = None,
    json_repair_attempt: int | None = None,
) -> ObservedLLM:
    """创建一个仅记录用量和调用维度的模型包装器。"""
    return ObservedLLM(
        model,
        LlmObservation(
            operation=operation,
            session_id=session_id,
            question_index=question_index,
            json_repair_attempt=json_repair_attempt,
        ),
    )


def extract_usage(response: Any) -> dict[str, int | str]:
    """优先读取保留 DeepSeek 自定义字段的原始 token_usage。"""
    response_metadata = _mapping(getattr(response, "response_metadata", None))
    token_usage = _mapping(response_metadata.get("token_usage"))
    usage_metadata = _mapping(getattr(response, "usage_metadata", None))

    prompt_tokens = _first_int(token_usage.get("prompt_tokens"), usage_metadata.get("input_tokens"))
    completion_tokens = _first_int(
        token_usage.get("completion_tokens"),
        usage_metadata.get("output_tokens"),
    )
    cache_hit = _first_int(
        token_usage.get("prompt_cache_hit_tokens"),
        response_metadata.get("prompt_cache_hit_tokens"),
        usage_metadata.get("prompt_cache_hit_tokens"),
    )
    cache_miss = _first_int(
        token_usage.get("prompt_cache_miss_tokens"),
        response_metadata.get("prompt_cache_miss_tokens"),
        usage_metadata.get("prompt_cache_miss_tokens"),
    )

    # 兼容标准 OpenAI cached_tokens 映射，但不在缺少供应商字段时
    # 自行推导 miss，避免把不同供应商的语义当成 DeepSeek 实测值。
    if cache_hit is None:
        prompt_details = _mapping(token_usage.get("prompt_tokens_details"))
        input_details = _mapping(usage_metadata.get("input_token_details"))
        cache_hit = _first_int(prompt_details.get("cached_tokens"), input_details.get("cache_read"))

    return {
        "prompt_tokens": prompt_tokens if prompt_tokens is not None else UNAVAILABLE,
        "prompt_cache_hit_tokens": cache_hit if cache_hit is not None else UNAVAILABLE,
        "prompt_cache_miss_tokens": cache_miss if cache_miss is not None else UNAVAILABLE,
        "completion_tokens": completion_tokens if completion_tokens is not None else UNAVAILABLE,
    }


def _log_usage(
    observation: LlmObservation,
    *,
    response: Any,
    model: Any,
    streaming: bool,
    elapsed_ms: float,
    error: Exception | None = None,
) -> None:
    """输出不包含提示词正文的单条结构化模型用量日志。"""
    usage = extract_usage(response) if response is not None else extract_usage(None)
    cache_hit = usage["prompt_cache_hit_tokens"]
    cache_miss = usage["prompt_cache_miss_tokens"]
    cache_hit_rate = (
        round(cache_hit / (cache_hit + cache_miss), 6)
        if isinstance(cache_hit, int)
        and isinstance(cache_miss, int)
        and cache_hit + cache_miss > 0
        else UNAVAILABLE
    )
    record: dict[str, Any] = {
        "operation": observation.operation,
        "model": _model_name(response, model),
        "session_id": observation.session_id if observation.session_id is not None else UNAVAILABLE,
        "question_index": (
            observation.question_index if observation.question_index is not None else UNAVAILABLE
        ),
        **usage,
        "cache_hit_rate": cache_hit_rate,
        "json_repair_attempt": (
            observation.json_repair_attempt
            if observation.json_repair_attempt is not None
            else False
        ),
        # langchain-openai 0.2.14 不暴露实际传输重试次数，不作猜测。
        "transport_retry": UNAVAILABLE,
        "streaming": streaming,
        "elapsed_ms": round(elapsed_ms, 1),
        "status": "error" if error else "ok",
    }
    if error is not None:
        record["error_type"] = type(error).__name__
    logger.info("llm.usage %s", json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def _has_usage(response: Any) -> bool:
    """判断响应或流式分块是否携带可观测的用量字段。"""
    metadata = _mapping(getattr(response, "response_metadata", None))
    return bool(metadata.get("token_usage") or getattr(response, "usage_metadata", None))


def _mapping(value: Any) -> Mapping[str, Any]:
    """只接受映射类型，其他输入安全退化为空映射。"""
    return value if isinstance(value, Mapping) else {}


def _first_int(*values: Any) -> int | None:
    """返回第一个可靠整数，拒绝把布尔值误当成 Token 数。"""
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def _model_name(response: Any, model: Any) -> str:
    """优先从响应读取实际模型名，再尝试解析绑定模型。"""
    response_metadata = _mapping(getattr(response, "response_metadata", None))
    name = response_metadata.get("model_name") or response_metadata.get("model")
    if name:
        return str(name)
    current = model
    for _ in range(4):
        name = getattr(current, "model_name", None) or getattr(current, "model", None)
        if isinstance(name, str) and name:
            return name
        current = getattr(current, "bound", None) or getattr(current, "_model", None)
        if current is None:
            break
    return UNAVAILABLE
