import logging

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeDocument


logger = logging.getLogger(__name__)


async def delete_knowledge_documents_by_marker(db: AsyncSession, marker: str) -> int:
    """删除标题、正文或元数据中包含指定标记的测试知识文档，并返回删除数量。"""
    if not marker:
        return 0

    result = await db.execute(
        delete(KnowledgeDocument).where(
            or_(
                KnowledgeDocument.title.contains(marker),
                KnowledgeDocument.content.contains(marker),
                KnowledgeDocument.metadata_json.contains(marker),
            )
        )
    )
    deleted_count = int(result.rowcount or 0)
    logger.info("knowledge.cleanup.marker marker=%r deleted_count=%s", marker, deleted_count)
    return deleted_count
