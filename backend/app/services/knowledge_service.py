import asyncio
import json
import logging
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeDocument, KnowledgeReindexJob
from app.rag.document_loader import document_to_chunks
from app.rag.retriever import (
    create_collection,
    delete_collection,
    delete_document_chunks,
    get_active_collection_target,
    replace_document_chunks,
    search_chunks,
    switch_active_collection,
    upsert_chunks,
)
from app.schemas.citation import KnowledgeCitation, KnowledgeContextResult
from app.schemas.knowledge import KnowledgeDocumentCreate, KnowledgeDocumentRead, KnowledgeDocumentUpdate, KnowledgeReindexResult
from app.services.knowledge_file_service import get_document_parser, normalize_extracted_text
from app.services.knowledge_quality import KnowledgeQualityError, validate_knowledge_text
from app.services.prompt_security import format_untrusted_data
from app.storage.oss_client import OssObjectStorage, build_knowledge_oss_key


logger = logging.getLogger(__name__)
CONTENT_PREVIEW_CHARS = 8000
REINDEX_LOCK_NAME = "knowledge_full_reindex"
REINDEX_RECOVERY = "Active alias remains on the previous collection; retry creates a fresh collection."
_process_reindex_lock = asyncio.Lock()
OperationResult = TypeVar("OperationResult")

KNOWLEDGE_CATEGORIES = {
    "planning": ["岗位能力", "面试计划", "planning"],
    "scoring": ["评分标准", "scoring", "rubric"],
    "question": ["面试题库", "常见追问", "question"],
    "answer": ["评分标准", "scoring", "rubric", "面试题库", "常见追问", "question"],
    "report": ["岗位能力", "评分标准", "优秀回答样例", "report"],
}
LEGACY_FILE_CATEGORIES = ["md", "txt", "pdf"]


class KnowledgeIndexingError(RuntimeError):
    def __init__(self, document_id: int, reason: str) -> None:
        """保存索引失败的文档标识与原因，供接口返回失败详情。"""
        super().__init__(reason)
        self.document_id = document_id
        self.reason = reason


class KnowledgeReindexConflict(RuntimeError):
    pass


class KnowledgeDataQualityError(RuntimeError):
    def __init__(self, document_id: int, reason: str) -> None:
        """保存质量检查失败的文档标识与原因，供接口返回失败详情。"""
        super().__init__(reason)
        self.document_id = document_id
        self.reason = reason


async def create_knowledge_document_and_commit(
    db: AsyncSession,
    payload: KnowledgeDocumentCreate,
) -> KnowledgeDocumentRead:
    """创建知识文档，并提交成功状态或可重试的失败状态。"""
    return await _commit_index_operation(db, create_knowledge_document(db, payload))


async def create_uploaded_knowledge_document_and_commit(
    db: AsyncSession,
    parsed_upload: Any,
) -> KnowledgeDocumentRead:
    """创建上传文档，并提交成功状态或可重试的失败状态。"""
    return await _commit_index_operation(
        db,
        create_uploaded_knowledge_document(db, parsed_upload),
    )


async def update_knowledge_document_and_commit(
    db: AsyncSession,
    document_id: int,
    payload: KnowledgeDocumentUpdate,
) -> KnowledgeDocumentRead | None:
    """更新知识文档并提交索引同步结果。"""
    return await _commit_index_operation(
        db,
        update_knowledge_document(db, document_id, payload),
    )


async def delete_knowledge_document_and_commit(
    db: AsyncSession,
    document_id: int,
) -> bool:
    """删除知识文档并提交向量索引同步结果。"""
    return await _commit_index_operation(db, delete_knowledge_document(db, document_id))


async def retry_knowledge_document_index_and_commit(
    db: AsyncSession,
    document_id: int,
) -> KnowledgeDocumentRead | None:
    """重试单篇文档索引并提交最新状态。"""
    return await _commit_index_operation(
        db,
        retry_knowledge_document_index(db, document_id),
    )


async def reindex_knowledge_documents_and_commit(
    db: AsyncSession,
) -> KnowledgeReindexResult:
    """重建全量索引并提交任务结果。"""
    result = await reindex_knowledge_documents(db)
    await db.commit()
    return result


async def _commit_index_operation(
    db: AsyncSession,
    operation: Awaitable[OperationResult],
) -> OperationResult:
    """统一提交索引操作；失败状态同样需要落库，随后保留原异常交给 API 映射。"""
    try:
        result = await operation
    except (KnowledgeDataQualityError, KnowledgeIndexingError):
        await db.commit()
        raise
    await db.commit()
    return result


def _validated_payload(payload: KnowledgeDocumentCreate) -> KnowledgeDocumentCreate:
    """校验知识正文质量，并在复制的请求数据中附加质量报告。"""
    report = validate_knowledge_text(payload.content)
    metadata = dict(payload.metadata)
    metadata["quality_report"] = report.to_metadata()
    return payload.model_copy(update={"metadata": metadata})


def _failed_quality_metadata(payload: KnowledgeDocumentCreate, exc: KnowledgeQualityError) -> str:
    """将质量检查失败的报告合并到文档元数据并序列化保存。"""
    metadata = dict(payload.metadata)
    metadata["quality_report"] = exc.report.to_metadata()
    return json.dumps(metadata, ensure_ascii=False)


def _content_preview(content: str) -> str:
    """按配置的字符上限截取知识正文预览。"""
    return content[:CONTENT_PREVIEW_CHARS]


def _document_payload(document: KnowledgeDocument, content: str | None = None) -> KnowledgeDocumentCreate:
    """将持久化文档转换为知识写入模型，允许传入恢复后的完整正文。"""
    return KnowledgeDocumentCreate(
        title=document.title,
        category=document.category,
        target_position=document.target_position,
        content=content if content is not None else document.content,
        metadata=json.loads(document.metadata_json or "{}"),
    )


def _prepare_chunks(document: KnowledgeDocument, payload: KnowledgeDocumentCreate) -> list[dict]:
    """切分文档并补充文档标识、稳定点标识、版本及对象存储来源信息。"""
    chunks = document_to_chunks(payload)
    for chunk in chunks:
        chunk["document_id"] = document.id
        chunk["point_id"] = f"{document.id}-{document.index_version}-{chunk['chunk_index']}"
        chunk["document_status"] = "ready"
        chunk["index_version"] = document.index_version
        if document.original_oss_key:
            chunk["original_oss_key"] = document.original_oss_key
        if document.parsed_text_oss_key:
            chunk["parsed_text_oss_key"] = document.parsed_text_oss_key
    return chunks


async def create_knowledge_document(db: AsyncSession, payload: KnowledgeDocumentCreate) -> KnowledgeDocumentRead:
    """创建知识文档并校验质量、生成索引，记录失败原因或返回已就绪的文档。"""
    document = KnowledgeDocument(
        title=payload.title,
        category=payload.category,
        target_position=payload.target_position,
        content=payload.content,
        metadata_json=json.dumps(payload.metadata, ensure_ascii=False),
        parse_status="indexing",
        index_version=1,
    )
    db.add(document)
    await db.flush()
    try:
        payload = _validated_payload(payload)
    except KnowledgeQualityError as exc:
        document.metadata_json = _failed_quality_metadata(payload, exc)
        document.parse_status = "failed"
        document.parse_error = f"KnowledgeQualityError: {exc}"[:2000]
        await db.flush()
        raise KnowledgeDataQualityError(document.id, str(exc)) from exc

    document.metadata_json = json.dumps(payload.metadata, ensure_ascii=False)
    chunks = _prepare_chunks(document, payload)
    document.chunk_count = len(chunks)
    try:
        await upsert_chunks(chunks)
    except Exception as exc:
        await _mark_index_failed(db, document, exc)
        raise KnowledgeIndexingError(document.id, document.parse_error or "Knowledge indexing failed") from exc
    document.parse_status = "ready"
    document.parse_error = None
    await db.flush()
    return _to_read(document)


async def create_uploaded_knowledge_document(db: AsyncSession, parsed_upload) -> KnowledgeDocumentRead:
    """校验上传正文并保存原文件及解析文本到对象存储，创建文档记录并建立向量索引。"""
    try:
        quality_report = validate_knowledge_text(parsed_upload.content)
    except KnowledgeQualityError as exc:
        raise KnowledgeDataQualityError(0, str(exc)) from exc
    parsed_upload.metadata["quality_report"] = quality_report.to_metadata()
    storage = OssObjectStorage()
    original_key = build_knowledge_oss_key("original", parsed_upload.filename)
    parsed_key = build_knowledge_oss_key("parsed", f"{Path(parsed_upload.filename).stem}.txt")
    await storage.upload_bytes(parsed_upload.original_content, original_key)
    await storage.upload_bytes(parsed_upload.content.encode("utf-8"), parsed_key)
    metadata = dict(parsed_upload.metadata)
    metadata.update(
        {
            "oss_bucket": settings.aliyun_oss_bucket,
            "original_oss_key": original_key,
            "parsed_text_oss_key": parsed_key,
            "file_size": parsed_upload.file_size,
        }
    )
    payload = KnowledgeDocumentCreate(
        title=parsed_upload.title,
        category=parsed_upload.category,
        target_position=parsed_upload.target_position,
        content=parsed_upload.content,
        metadata=metadata,
    )
    document = KnowledgeDocument(
        title=payload.title,
        category=payload.category,
        target_position=payload.target_position,
        content=_content_preview(payload.content),
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        original_oss_key=original_key,
        parsed_text_oss_key=parsed_key,
        file_name=parsed_upload.filename,
        file_type=parsed_upload.file_type,
        file_size=parsed_upload.file_size,
        parse_status="indexing",
        index_version=1,
    )
    db.add(document)
    await db.flush()
    chunks = _prepare_chunks(document, payload)
    document.chunk_count = len(chunks)
    try:
        await upsert_chunks(chunks)
    except Exception as exc:
        await _mark_index_failed(db, document, exc)
        raise KnowledgeIndexingError(document.id, document.parse_error or "Knowledge indexing failed") from exc
    document.parse_status = "ready"
    document.parse_error = None
    await db.flush()
    return _to_read(document)


async def list_knowledge_documents(db: AsyncSession) -> list[KnowledgeDocumentRead]:
    """按创建时间倒序读取知识文档，并转换为接口返回模型。"""
    result = await db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()))
    return [_to_read(document) for document in result]


async def get_knowledge_document(db: AsyncSession, document_id: int) -> KnowledgeDocumentRead | None:
    """按标识读取单个知识文档，不存在时返回空值。"""
    document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    return _to_read(document) if document else None


async def _load_document_content(document: KnowledgeDocument) -> str:
    """优先从对象存储加载解析正文，存储暂时不可用时尝试使用数据库中的正文副本。"""
    if not document.parsed_text_oss_key:
        return document.content
    try:
        return (await OssObjectStorage().download_bytes(document.parsed_text_oss_key)).decode("utf-8")
    except Exception:
        # 上传文档会保留最多 8000 个字符的数据库副本，可在对象存储暂时不可用时恢复正文；
        # 恢复的正文会在后续流程中重新接受质量检查。
        if document.content:
            logger.warning(
                "knowledge.content.oss_unavailable_using_database_copy document_id=%s",
                document.id,
                exc_info=True,
            )
            return document.content
        raise


async def _load_validated_document_payload(document: KnowledgeDocument) -> KnowledgeDocumentCreate:
    """校验已保存的文本，并在条件允许时重新解析原始对象以修复正文。"""
    content = await _load_document_content(document)
    payload = _document_payload(document, content)
    try:
        return _validated_payload(payload)
    except KnowledgeQualityError as original_error:
        if not document.original_oss_key or not document.file_name:
            raise original_error

        try:
            suffix = Path(document.file_name).suffix.lower()
            original = await OssObjectStorage().download_bytes(document.original_oss_key)
            reparsed = normalize_extracted_text(get_document_parser(suffix).parse(original, document.file_name))
            repaired = _validated_payload(_document_payload(document, reparsed))
        except Exception:
            logger.warning("knowledge.quality.reparse.failed document_id=%s", document.id, exc_info=True)
            raise original_error
        document.content = _content_preview(reparsed)
        document.metadata_json = json.dumps(repaired.metadata, ensure_ascii=False)
        if document.parsed_text_oss_key:
            await OssObjectStorage().upload_bytes(reparsed.encode("utf-8"), document.parsed_text_oss_key)
        return repaired


async def _quarantine_quality_failure(
    db: AsyncSession,
    document: KnowledgeDocument,
    exc: KnowledgeQualityError,
) -> None:
    """记录文档质量失败信息并清空分块数量，尝试删除旧索引以隔离无效内容。"""
    payload = _document_payload(document)
    document.metadata_json = _failed_quality_metadata(payload, exc)
    document.parse_status = "failed"
    document.parse_error = f"KnowledgeQualityError: {exc}"[:2000]
    document.chunk_count = 0
    try:
        await delete_document_chunks(document.id)
    except Exception:
        logger.warning("knowledge.quality.delete_stale.failed document_id=%s", document.id, exc_info=True)
    await db.flush()


async def retry_knowledge_document_index(db: AsyncSession, document_id: int) -> KnowledgeDocumentRead | None:
    """重新校验或恢复文档正文并替换索引，按失败类型记录状态并抛出对应异常。"""
    document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    if not document:
        return None
    try:
        payload = await _load_validated_document_payload(document)
        chunks = _prepare_chunks(document, payload)
        document.parse_status = "indexing"
        document.parse_error = None
        document.metadata_json = json.dumps(payload.metadata, ensure_ascii=False)
        document.chunk_count = len(chunks)
        await db.flush()
        await replace_document_chunks(document.id, document.index_version, chunks)
    except KnowledgeQualityError as exc:
        await _quarantine_quality_failure(db, document, exc)
        raise KnowledgeDataQualityError(document.id, str(exc)) from exc
    except Exception as exc:
        await _mark_index_failed(db, document, exc)
        raise KnowledgeIndexingError(document.id, document.parse_error or "Knowledge indexing failed") from exc
    document.parse_status = "ready"
    document.parse_error = None
    await db.flush()
    return _to_read(document)


async def _mark_index_failed(db: AsyncSession, document: KnowledgeDocument, exc: Exception) -> None:
    """将文档标记为索引失败，保存受长度限制的异常信息并记录日志。"""
    document.parse_status = "failed"
    document.parse_error = f"{type(exc).__name__}: {exc}"[:2000]
    await db.flush()
    logger.exception("knowledge.index.failed document_id=%s", document.id, exc_info=exc)


async def update_knowledge_document(
    db: AsyncSession,
    document_id: int,
    payload: KnowledgeDocumentUpdate,
) -> KnowledgeDocumentRead | None:
    """替换单个文档版本，无需重建索引或隐藏其他文档。"""
    document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    if not document:
        return None
    try:
        payload = _validated_payload(payload)
    except KnowledgeQualityError as exc:
        raise KnowledgeDataQualityError(document.id, str(exc)) from exc
    next_version = document.index_version + 1
    prospective = KnowledgeDocument(
        id=document.id,
        title=payload.title,
        category=payload.category,
        target_position=payload.target_position,
        content=payload.content,
        metadata_json=json.dumps(payload.metadata, ensure_ascii=False),
        index_version=next_version,
        parse_status="indexing",
    )
    chunks = _prepare_chunks(prospective, payload)
    try:
        await replace_document_chunks(document.id, next_version, chunks)
    except Exception as exc:
        document.parse_error = f"{type(exc).__name__}: {exc}"[:2000]
        await db.flush()
        raise KnowledgeIndexingError(document.id, document.parse_error) from exc

    document.title = payload.title
    document.category = payload.category
    document.target_position = payload.target_position
    document.content = payload.content
    document.metadata_json = json.dumps(payload.metadata, ensure_ascii=False)
    document.chunk_count = len(chunks)
    document.index_version = next_version
    document.parse_status = "ready"
    document.parse_error = None
    await db.flush()
    return _to_read(document)


async def delete_knowledge_document(db: AsyncSession, document_id: int) -> bool:
    """仅删除目标文档的向量点，保持活动集合在线。"""
    document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    if not document:
        return False
    try:
        await delete_document_chunks(document_id)
    except Exception as exc:
        document.parse_error = f"{type(exc).__name__}: {exc}"[:2000]
        await db.flush()
        raise KnowledgeIndexingError(document.id, document.parse_error) from exc
    await db.delete(document)
    await db.flush()
    return True


async def _acquire_reindex_lock(db: AsyncSession) -> Any | None:
    """尝试获取全量重建锁，MySQL 使用独立连接的命名锁，其他环境使用进程内锁。"""
    if db.bind and db.bind.dialect.name == "mysql":
        connection = await db.bind.connect()
        acquired = await connection.scalar(
            text("SELECT GET_LOCK(:name, 0)"),
            {"name": REINDEX_LOCK_NAME},
        )
        if acquired == 1:
            return connection
        await connection.close()
        return None
    if _process_reindex_lock.locked():
        return None
    await _process_reindex_lock.acquire()
    return True


async def _release_reindex_lock(lock_handle: Any) -> None:
    """释放对应类型的重建锁，并关闭持有数据库命名锁的连接。"""
    if lock_handle is True:
        if _process_reindex_lock.locked():
            _process_reindex_lock.release()
        return
    try:
        await lock_handle.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": REINDEX_LOCK_NAME})
    finally:
        await lock_handle.close()


async def reindex_knowledge_documents(db: AsyncSession) -> KnowledgeReindexResult:
    """构建新的物理集合后，原子切换活动别名以发布重建结果。"""
    lock_handle = await _acquire_reindex_lock(db)
    if lock_handle is None:
        raise KnowledgeReindexConflict("Another knowledge reindex job is already running")

    job: KnowledgeReindexJob | None = None
    try:
        source_collection = await get_active_collection_target()
        job = KnowledgeReindexJob(
            status="running",
            source_collection=source_collection,
            target_collection=f"{settings.qdrant_collection_name}__build_{uuid4().hex[:12]}",
            recovery_strategy=REINDEX_RECOVERY,
            started_at=datetime.utcnow(),
        )
        db.add(job)
        await db.commit()

        documents = list(
            await db.scalars(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.parse_status == "ready")
                .order_by(KnowledgeDocument.id.asc())
            )
        )
        valid_documents: list[tuple[KnowledgeDocument, KnowledgeDocumentCreate]] = []
        for document in documents:
            try:
                payload = await _load_validated_document_payload(document)
            except KnowledgeQualityError as exc:
                await _quarantine_quality_failure(db, document, exc)
                logger.warning("knowledge.reindex.quarantined document_id=%s reason=%s", document.id, exc)
                continue
            valid_documents.append((document, payload))

        job.total_documents = len(valid_documents)
        await db.commit()
        await create_collection(job.target_collection)

        for document, payload in valid_documents:
            chunks = _prepare_chunks(document, payload)
            await upsert_chunks(chunks, collection_name=job.target_collection)
            job.processed_documents += 1
            job.total_chunks += len(chunks)
            await db.commit()

        await switch_active_collection(job.target_collection)
        job.status = "completed"
        job.finished_at = datetime.utcnow()
        await db.commit()
    except Exception as exc:
        if job is None:
            await db.rollback()
            raise
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"[:2000]
        job.finished_at = datetime.utcnow()
        await db.commit()
        if job.target_collection:
            try:
                await delete_collection(job.target_collection)
            except Exception:
                logger.warning(
                    "knowledge.reindex.cleanup.failed collection=%s",
                    job.target_collection,
                    exc_info=True,
                )
    finally:
        await _release_reindex_lock(lock_handle)

    if job is None:
        raise RuntimeError("Reindex job was not initialized")
    return _to_reindex_result(job)

async def get_reindex_job(db: AsyncSession, job_id: int) -> KnowledgeReindexResult | None:
    """按任务标识读取知识索引重建进度，不存在时返回空值。"""
    job = await db.scalar(select(KnowledgeReindexJob).where(KnowledgeReindexJob.id == job_id))
    return _to_reindex_result(job) if job else None


async def search_knowledge(
    query: str,
    limit: int = 5,
    *,
    target_position: str | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    versions: list[int] | None = None,
    include_general: bool = True,
    legacy_title_scope: str | None = None,
) -> list[dict]:
    """按岗位、分类、状态及版本范围检索知识分块，默认只查询已就绪文档。"""
    return await search_chunks(
        query,
        limit=limit,
        target_position=target_position,
        categories=categories,
        statuses=statuses or ["ready"],
        versions=versions,
        include_general=include_general,
        legacy_title_scope=legacy_title_scope,
    )


async def format_knowledge_context(
    query: str,
    limit: int = 5,
    *,
    target_position: str | None = None,
    purpose: str | None = None,
    categories: list[str] | None = None,
    include_general: bool = True,
    with_citations: bool = False,
) -> str | KnowledgeContextResult:
    """按调用用途检索知识并兼容旧分类，组装不可信数据上下文，按需附带可追溯引用。"""
    selected_categories = categories if categories is not None else KNOWLEDGE_CATEGORIES.get(purpose or "")
    chunks = await search_knowledge(
        query,
        limit=limit,
        target_position=target_position,
        categories=selected_categories,
        include_general=include_general,
    )
    if not chunks and categories is None and selected_categories:
        logger.warning(
            "knowledge.search.legacy_category_fallback purpose=%s target_position=%s",
            purpose,
            target_position,
        )
        chunks = await search_knowledge(
            query,
            limit=limit,
            target_position=target_position,
            categories=LEGACY_FILE_CATEGORIES,
            include_general=include_general,
            legacy_title_scope=target_position if target_position and target_position != "general" else None,
        )
    if not chunks:
        empty = KnowledgeContextResult(text="No relevant knowledge base content.", citations=[])
        return empty if with_citations else empty.text
    lines = []
    citations: list[KnowledgeCitation] = []
    for index, chunk in enumerate(chunks, start=1):
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
        lines.append(
            format_untrusted_data(
                "knowledge_base_chunk",
                {
                    "reference": index,
                    "source": " | ".join(source_parts),
                    "text": str(chunk.get("text") or chunk.get("content") or ""),
                },
            )
        )
        if with_citations:
            citations.append(_knowledge_citation(chunk, reference=index, query=query, purpose=purpose))
    result = KnowledgeContextResult(text="\n".join(lines), citations=citations)
    return result if with_citations else result.text


def unpack_knowledge_context(value: str | KnowledgeContextResult | dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """统一拆解结构化检索结果，同时兼容返回纯文本的测试替身。"""
    if isinstance(value, KnowledgeContextResult):
        return value.text, [citation.model_dump(mode="json") for citation in value.citations]
    if isinstance(value, dict) and "text" in value:
        result = KnowledgeContextResult.model_validate(value)
        return result.text, [citation.model_dump(mode="json") for citation in result.citations]
    return str(value), []


def _knowledge_citation(
    chunk: dict[str, Any],
    *,
    reference: int,
    query: str,
    purpose: str | None,
) -> KnowledgeCitation:
    """从召回分块构建知识引用快照，包含来源、版本、排名、重排信息与上下文预算记录。"""
    metadata = chunk.get("metadata") or {}
    rerank = chunk.get("_rerank") or {}
    context_chunks = chunk.get("_context_chunks") or [_fallback_context_chunk(chunk)]
    primary_context = next((item for item in context_chunks if item.get("is_primary")), context_chunks[0])
    rerank_score = float(chunk.get("_rerank_score") or 0.0)
    rrf_score = float(chunk.get("_rrf_score") or 0.0)
    return KnowledgeCitation(
        reference=reference,
        query=query,
        purpose=purpose,
        document_id=int(chunk.get("document_id") or 0),
        title=str(chunk.get("title") or "Untitled"),
        category=str(chunk.get("category") or "Uncategorized"),
        target_position=str(chunk.get("target_position") or "Unknown position"),
        index_version=int(chunk.get("index_version") or 1),
        chunk_id=str(primary_context.get("chunk_id") or chunk.get("point_id") or ""),
        chunk_index=int(primary_context.get("chunk_index") or 0),
        source_page=_optional_positive_int(primary_context.get("source_page")),
        section_title=str(chunk.get("section_title") or metadata.get("section_title") or "") or None,
        retrieval_score=rerank_score or rrf_score,
        retrieval_routes=[str(item) for item in chunk.get("_retrieval_routes") or []],
        retrieval_scores={
            str(route): float(score)
            for route, score in (chunk.get("_retrieval_scores") or {}).items()
        },
        retrieval_ranks={
            str(route): int(rank)
            for route, rank in (chunk.get("_retrieval_ranks") or {}).items()
        },
        rrf_score=rrf_score,
        rerank_score=rerank_score,
        rerank_provider=str(rerank.get("provider") or "") or None,
        rerank_model=str(rerank.get("model") or "") or None,
        rerank_rank=_optional_positive_int(rerank.get("rank")),
        rerank_is_fallback=bool(rerank.get("is_fallback", False)),
        rerank_fallback_reason=str(rerank.get("fallback_reason") or "") or None,
        context_chunks=context_chunks,
        context_token_count=int(chunk.get("_context_token_count") or 0),
        context_truncated=bool(chunk.get("_context_truncated", False)),
    )


def _fallback_context_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    """为缺少扩展上下文的分块构造最小来源信息，并标记为主分块。"""
    metadata = chunk.get("metadata") or {}
    return {
        "chunk_id": str(chunk.get("point_id") or ""),
        "chunk_index": int(chunk.get("chunk_index") or 0),
        "source_page": _optional_positive_int(chunk.get("source_page") or metadata.get("source_page")),
        "is_primary": True,
        "context_token_count": int(chunk.get("_context_token_count") or 0),
        "context_truncated": bool(chunk.get("_context_truncated", False)),
    }


def _optional_positive_int(value: Any) -> int | None:
    """将非空输入转换为正整数，空值和非正数返回空值。"""
    if value in (None, ""):
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _to_reindex_result(job: KnowledgeReindexJob) -> KnowledgeReindexResult:
    """将重建任务的状态、进度、集合及恢复信息转换为接口返回模型。"""
    return KnowledgeReindexResult(
        status=job.status,
        job_id=job.id,
        document_count=job.total_documents,
        chunk_count=job.total_chunks,
        processed_documents=job.processed_documents,
        source_collection=job.source_collection,
        target_collection=job.target_collection,
        error=job.error,
        recovery_strategy=job.recovery_strategy,
    )


def _to_read(document: KnowledgeDocument) -> KnowledgeDocumentRead:
    """将数据库知识文档转换为返回模型，并将文件和索引字段补入缺失的元数据项。"""
    metadata = json.loads(document.metadata_json or "{}")
    for key in (
        "original_oss_key",
        "parsed_text_oss_key",
        "file_name",
        "file_type",
        "file_size",
        "chunk_count",
        "index_version",
        "parse_status",
        "parse_error",
    ):
        value = getattr(document, key, None)
        if value is not None:
            metadata.setdefault(key, value)
    return KnowledgeDocumentRead(
        id=document.id,
        title=document.title,
        category=document.category,
        target_position=document.target_position,
        content=document.content,
        metadata=metadata,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )
