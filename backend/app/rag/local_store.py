from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LocalKnowledgeChunk:
    document_id: int
    point_id: str
    text: str
    title: str
    category: str
    target_position: str
    metadata: dict = field(default_factory=dict)
    chunk_index: int = 0


LOCAL_KNOWLEDGE_CHUNKS: list[LocalKnowledgeChunk] = []


def register_local_chunks(chunks: list[dict]) -> None:
    """将分块载荷新增或更新到当前进程的本地兜底索引。"""
    point_ids = {str(chunk.get("point_id") or "") for chunk in chunks}
    point_ids.discard("")
    if point_ids:
        LOCAL_KNOWLEDGE_CHUNKS[:] = [
            chunk for chunk in LOCAL_KNOWLEDGE_CHUNKS if chunk.point_id not in point_ids
        ]
    # 保存一份进程内 fallback 索引，Qdrant 不可用时还能做简单检索。
    for chunk in chunks:
        LOCAL_KNOWLEDGE_CHUNKS.append(
            LocalKnowledgeChunk(
                document_id=int(chunk.get("document_id") or 0),
                point_id=str(chunk.get("point_id") or ""),
                text=str(chunk.get("text") or ""),
                title=str(chunk.get("title") or ""),
                category=str(chunk.get("category") or ""),
                target_position=str(chunk.get("target_position") or ""),
                metadata=_build_local_metadata(chunk),
                chunk_index=int(chunk.get("chunk_index") or 0),
            )
        )


def search_local_chunks(query: str, limit: int = 5) -> list[dict]:
    """在本地 fallback 索引上做轻量关键词检索。"""
    # 一个很轻量的关键词匹配 fallback，先保证知识库测试和 prompt 注入能跑通。
    if not query:
        return []

    query_terms = [term for term in query.lower().split() if term]
    scored: list[tuple[int, LocalKnowledgeChunk]] = []

    for chunk in LOCAL_KNOWLEDGE_CHUNKS:
        haystack = f"{chunk.title} {chunk.category} {chunk.target_position} {chunk.text}".lower()
        score = sum(1 for term in query_terms if term in haystack)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: (-item[0], item[1].document_id, item[1].chunk_index))
    return [
        {
            "document_id": chunk.document_id,
            "point_id": chunk.point_id,
            "text": chunk.text,
            "title": chunk.title,
            "category": chunk.category,
            "target_position": chunk.target_position,
            "metadata": chunk.metadata,
            "chunk_index": chunk.chunk_index,
        }
        for _score, chunk in scored[:limit]
    ]



def _build_local_metadata(chunk: dict) -> dict:
    """把 chunk 顶层的结构化信息合并到本地索引 metadata 中。"""
    metadata = dict(chunk.get("metadata") or {})
    for key in ("section_title", "heading_path", "source_page"):
        if key in chunk and key not in metadata:
            metadata[key] = chunk[key]
    return metadata
