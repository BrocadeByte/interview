import math
import re
from typing import Any

from app.services.prompt_security import format_untrusted_data


def knowledge_source_label(chunk: dict[str, Any]) -> str:
    """生成进入最终知识 JSON 的稳定、必要来源信息。"""
    metadata = chunk.get("metadata") or {}
    source_parts = [
        f"title={chunk.get('title', 'Untitled')}",
        f"category={chunk.get('category', 'Uncategorized')}",
        f"position={chunk.get('target_position', 'Unknown position')}",
        f"version={chunk.get('index_version', 1)}",
    ]
    if chunk.get("section_title") or metadata.get("section_title"):
        source_parts.append(f"section={chunk.get('section_title') or metadata.get('section_title')}")
    if chunk.get("source_page") or metadata.get("source_page"):
        source_parts.append(f"page={chunk.get('source_page') or metadata.get('source_page')}")
    return " | ".join(source_parts)


def format_knowledge_chunk(
    reference: int,
    chunk: dict[str, Any],
    *,
    text: str | None = None,
) -> str:
    """只序列化一次知识分块，结果可直接放入模型消息。"""
    return format_untrusted_data(
        "knowledge_base_chunk",
        {
            "reference": reference,
            "source": knowledge_source_label(chunk),
            "text": str(text if text is not None else chunk.get("text") or chunk.get("content") or ""),
        },
    )


def estimate_context_tokens(text: str) -> int:
    """保守估算最终提示文本的 Token：汉字逐字、其他字符每四个计一个。"""
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    non_cjk = re.sub(r"[\u4e00-\u9fff]", "", text)
    non_cjk_tokens = math.ceil(len(non_cjk) / 4) if non_cjk else 0
    return cjk + non_cjk_tokens
