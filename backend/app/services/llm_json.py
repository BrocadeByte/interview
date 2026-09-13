import json
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.services.llm_service import bind_json_output
from app.services.llm_usage import observe_llm
from app.services.prompt_security import secure_system_prompt


REPAIR_SYSTEM_PROMPT = """
你是 JSON 修复器。你只负责把模型输出修复为符合要求的合法 JSON 对象。
要求：
1. 只能输出 JSON 对象本身，不要输出 Markdown、解释、代码块。
2. 不要新增和任务无关的信息。
3. 尽量保留原始输出中的语义。
""".strip()


LlmOutputT = TypeVar("LlmOutputT", bound=BaseModel)


def parse_json_object(content: Any) -> dict[str, Any]:
    """从模型输出中解析 JSON 对象，并兼容代码块或前后夹杂文本的情况。"""
    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])

    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    return data


async def parse_json_object_with_repair(
    content: Any,
    *,
    llm: Any,
    expected_schema: str,
    max_retries: int = 1,
    observation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析模型输出；失败时调用 LLM 按预期结构修复，并在重试耗尽后抛出原异常。"""
    last_error: Exception | None = None
    raw_content = str(content)

    for attempt in range(max_retries + 1):
        try:
            return parse_json_object(raw_content)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break

            repair_prompt = f"""
期望 JSON 结构：
{expected_schema}

解析错误：
{type(exc).__name__}: {exc}

需要修复的原始输出：
{raw_content}
""".strip()
            repair_llm = _observed_repair_llm(llm, attempt + 1, observation_context)
            repaired = await repair_llm.ainvoke([
                SystemMessage(content=secure_system_prompt(REPAIR_SYSTEM_PROMPT)),
                HumanMessage(content=repair_prompt),
            ])
            raw_content = str(repaired.content)

    if last_error:
        raise last_error
    raise ValueError("LLM response must be a JSON object")


async def parse_json_model_with_repair(
    content: Any,
    *,
    llm: Any,
    output_model: type[LlmOutputT],
    max_retries: int = 1,
    observation_context: dict[str, Any] | None = None,
) -> LlmOutputT:
    """解析并校验模型返回的 JSON 对象，默认在语法或结构校验失败后尝试一次修复。"""
    last_error: Exception | None = None
    raw_content = str(content)
    schema = json.dumps(output_model.model_json_schema(), ensure_ascii=False)

    for attempt in range(max_retries + 1):
        try:
            data = parse_json_object(raw_content)
            return output_model.model_validate(data)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break

            repair_prompt = f"""
期望 JSON Schema：
{schema}

解析或校验错误：
{type(exc).__name__}: {exc}

需要修复的原始输出：
{raw_content}
""".strip()
            repair_llm = _observed_repair_llm(llm, attempt + 1, observation_context)
            repaired = await repair_llm.ainvoke([
                SystemMessage(content=secure_system_prompt(REPAIR_SYSTEM_PROMPT)),
                HumanMessage(content=repair_prompt),
            ])
            raw_content = str(repaired.content)

    if last_error:
        raise last_error
    raise ValueError("LLM response must match the required JSON schema")


def _observed_repair_llm(
    model: Any,
    attempt: int,
    observation_context: dict[str, Any] | None,
):
    """为 JSON 修复器启用原生 JSON Output，并单独记录低复用请求。"""
    context = observation_context or {}
    return observe_llm(
        bind_json_output(model),
        operation="json_repair",
        session_id=context.get("session_id"),
        question_index=context.get("question_index"),
        json_repair_attempt=attempt,
    )


def as_str(value: Any, default: str = "") -> str:
    """将任意值转换为字符串，空值则返回指定默认值。"""
    if value is None:
        return default
    return str(value)


def as_list(value: Any) -> list[Any]:
    """将值规范化为列表，空值转为空列表，单个值包装为单元素列表。"""
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return [value]


def as_dict(value: Any) -> dict[str, Any]:
    """仅保留字典类型的值，其他类型统一返回空字典。"""
    if isinstance(value, dict):
        return value
    return {}


def clamp_int(value: Any, default: int = 0, min_value: int = 0, max_value: int = 100) -> int:
    """将值转换为整数并限制在给定区间，转换失败时使用默认值。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))
