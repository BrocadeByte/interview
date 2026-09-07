import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeDocument, KnowledgeIngestionTask
from app.rag.document_loader import document_to_chunks
from app.rag.retriever import upsert_chunks
from app.schemas.knowledge import KnowledgeDocumentCreate
from app.schemas.knowledge_task import KnowledgeIngestionTaskRead
from app.services.knowledge_file_service import (
    ALLOWED_KNOWLEDGE_SUFFIXES,
    MAX_KNOWLEDGE_FILE_BYTES,
    get_document_parser,
    normalize_extracted_text,
)
from app.services.knowledge_queue import publish_ingestion_task
from app.services.knowledge_quality import KnowledgeQualityError, validate_knowledge_text
from app.storage.oss_client import OssObjectStorage, build_knowledge_oss_key


logger = logging.getLogger(__name__)


async def submit_upload_task(
    db: AsyncSession,
    *,
    file: UploadFile,
    title: str | None,
    category: str,
    target_position: str | None,
) -> KnowledgeIngestionTaskRead:
    """校验上传文件、持久化摄取任务并发布到消息队列。"""
    filename = file.filename or "uploaded"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_KNOWLEDGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only .txt, .md and .pdf files are supported")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_KNOWLEDGE_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file is too large")

    task = await create_upload_task(
        db,
        title=(title or Path(filename).stem).strip(),
        category=category.strip(),
        target_position=(target_position or "general").strip() or "general",
        filename=filename,
        file_type=suffix[1:],
        file_size=len(content),
        original_content=content,
    )
    try:
        await publish_ingestion_task(task.id)
    except Exception as exc:
        await _mark_publish_failed(db, task, exc)
        raise HTTPException(
            status_code=503,
            detail="Task saved but RabbitMQ publish failed",
        ) from exc
    return ingestion_task_to_read(task)


async def list_ingestion_tasks(
    db: AsyncSession,
    task_status: str | None,
) -> list[KnowledgeIngestionTaskRead]:
    """按创建时间倒序返回摄取任务，可选按状态过滤。"""
    statement = select(KnowledgeIngestionTask).order_by(
        KnowledgeIngestionTask.created_at.desc()
    )
    if task_status:
        statement = statement.where(KnowledgeIngestionTask.status == task_status)
    tasks = await db.scalars(statement)
    return [ingestion_task_to_read(task) for task in tasks]


async def get_ingestion_task(
    db: AsyncSession,
    task_id: int,
) -> KnowledgeIngestionTask:
    """读取摄取任务，不存在时返回统一的 404。"""
    task = await db.scalar(
        select(KnowledgeIngestionTask).where(KnowledgeIngestionTask.id == task_id)
    )
    if not task:
        raise HTTPException(status_code=404, detail="Ingestion task not found")
    return task


async def retry_ingestion_task(
    db: AsyncSession,
    task_id: int,
    *,
    reset_attempts: bool,
) -> KnowledgeIngestionTaskRead:
    """重置可重试任务并重新发布；重执行成功任务时同时清零尝试次数。"""
    task = await get_ingestion_task(db, task_id)
    allowed = {"failed", "dead", "publish_failed"}
    if reset_attempts:
        allowed.add("succeeded")
    if task.status not in allowed:
        raise HTTPException(status_code=409, detail="Task cannot be retried in its current state")

    task.status = "pending"
    task.stage = "queued"
    task.error = None
    task.finished_at = None
    if reset_attempts:
        task.attempts = 0
    await db.commit()
    try:
        await publish_ingestion_task(task.id)
    except Exception as exc:
        await _mark_publish_failed(db, task, exc)
        raise HTTPException(
            status_code=503,
            detail="Task reset but RabbitMQ publish failed",
        ) from exc
    return ingestion_task_to_read(task)


def ingestion_task_to_read(task: KnowledgeIngestionTask) -> KnowledgeIngestionTaskRead:
    """把摄取任务 ORM 对象转换为接口响应模型。"""
    return KnowledgeIngestionTaskRead.model_validate(task, from_attributes=True)


async def _mark_publish_failed(
    db: AsyncSession,
    task: KnowledgeIngestionTask,
    exc: Exception,
) -> None:
    """持久化消息发布失败状态，供管理员稍后重试。"""
    task.status = "publish_failed"
    task.stage = "publish_failed"
    task.error = f"{type(exc).__name__}: {exc}"[:2000]
    await db.commit()


async def create_upload_task(
    db: AsyncSession,
    *,
    title: str,
    category: str,
    target_position: str,
    filename: str,
    file_type: str,
    file_size: int,
    original_content: bytes,
) -> KnowledgeIngestionTask:
    """上传原始文件到对象存储，并创建待处理的摄取任务。"""
    source_key = build_knowledge_oss_key("original", filename)
    await OssObjectStorage().upload_bytes(original_content, source_key)
    task = KnowledgeIngestionTask(
        title=title,
        category=category,
        target_position=target_position,
        file_name=filename,
        file_type=file_type,
        file_size=file_size,
        source_oss_key=source_key,
        max_attempts=settings.knowledge_ingestion_max_attempts,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def process_ingestion_task(db: AsyncSession, task_id: int) -> None:
    """执行知识文件下载、解析、质量检查、分块和索引入库，持久化各阶段状态及失败重试信息。"""
    task = await db.scalar(select(KnowledgeIngestionTask).where(KnowledgeIngestionTask.id == task_id))
    if not task or task.status in {"succeeded", "running"}:
        return

    task.status = "running"
    task.stage = "parsing"
    task.attempts += 1
    task.started_at = datetime.utcnow()
    task.error = None
    await db.commit()
    try:
        original = await OssObjectStorage().download_bytes(task.source_oss_key)
        parsed = normalize_extracted_text(get_document_parser(f".{task.file_type}").parse(original, task.file_name))
        quality = validate_knowledge_text(parsed)
        parsed_key = task.parsed_text_oss_key or build_knowledge_oss_key("parsed", f"{Path(task.file_name).stem}.txt")
        await OssObjectStorage().upload_bytes(parsed.encode("utf-8"), parsed_key)
        task.parsed_text_oss_key = parsed_key
        task.stage = "embedding"
        await db.commit()

        document = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == task.document_id)) if task.document_id else None
        if not document:
            metadata = {
                "source": "async_upload",
                "filename": task.file_name,
                "file_type": task.file_type,
                "quality_report": quality.to_metadata(),
                "original_oss_key": task.source_oss_key,
                "parsed_text_oss_key": parsed_key,
                "file_size": task.file_size,
            }
            document = KnowledgeDocument(
                title=task.title,
                category=task.category,
                target_position=task.target_position,
                content=parsed[:8000],
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                original_oss_key=task.source_oss_key,
                parsed_text_oss_key=parsed_key,
                file_name=task.file_name,
                file_type=task.file_type,
                file_size=task.file_size,
                parse_status="indexing",
                index_version=1,
            )
            db.add(document)
            await db.flush()
            task.document_id = document.id

        payload = KnowledgeDocumentCreate(
            title=task.title,
            category=task.category,
            target_position=task.target_position,
            content=parsed,
            metadata=json.loads(document.metadata_json or "{}"),
        )
        chunks = document_to_chunks(payload)
        for chunk in chunks:
            chunk.update(
                document_id=document.id,
                point_id=f"{document.id}-{document.index_version}-{chunk['chunk_index']}",
                document_status="ready",
                index_version=document.index_version,
                original_oss_key=task.source_oss_key,
                parsed_text_oss_key=parsed_key,
            )
        document.chunk_count = len(chunks)
        task.stage = "indexing"
        await db.commit()
        try:
            await upsert_chunks(chunks)
        except Exception as exc:
            document.parse_status = "failed"
            document.parse_error = f"{type(exc).__name__}: {exc}"[:2000]
            await db.commit()
            raise
        document.parse_status = "ready"
        document.parse_error = None
        task.status = "succeeded"
        task.stage = "completed"
        task.finished_at = datetime.utcnow()
        await db.commit()
    except KnowledgeQualityError as exc:
        task.status = "dead"
        task.stage = "quality_failed"
        task.error = f"KnowledgeQualityError: {exc}"[:2000]
        task.finished_at = datetime.utcnow()
        await db.commit()
        raise
    except Exception as exc:
        task.status = "failed" if task.attempts < task.max_attempts else "dead"
        task.stage = "failed"
        task.error = f"{type(exc).__name__}: {exc}"[:2000]
        task.finished_at = datetime.utcnow()
        await db.commit()
        logger.exception("knowledge.ingestion.failed task_id=%s", task_id)
        raise
