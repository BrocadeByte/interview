"""异步知识摄取 HTTP 接口：负责管理员权限和表单参数适配。"""

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.core.database import get_db
from app.models.knowledge import KnowledgeIngestionTask
from app.models.user import User
from app.schemas.knowledge_task import KnowledgeIngestionTaskRead
from app.services.knowledge_ingestion_service import (
    get_ingestion_task as get_ingestion_task_service,
    ingestion_task_to_read,
    list_ingestion_tasks as list_ingestion_tasks_service,
    retry_ingestion_task as retry_ingestion_task_service,
    submit_upload_task,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge-ingestion"])


@router.post("/files/async", response_model=KnowledgeIngestionTaskRead, status_code=status.HTTP_202_ACCEPTED)
async def upload_document_file_async(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    category: str = Form(...),
    target_position: str | None = Form(default=None),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeIngestionTaskRead:
    """保存知识文件并提交异步解析、向量化任务。"""
    del current_user
    return await submit_upload_task(
        db,
        file=file,
        title=title,
        category=category,
        target_position=target_position,
    )


@router.get("/ingestion-tasks", response_model=list[KnowledgeIngestionTaskRead])
async def list_ingestion_tasks(
    task_status: str | None = None,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeIngestionTaskRead]:
    """返回知识摄取任务列表，可按状态筛选。"""
    del current_user
    return await list_ingestion_tasks_service(db, task_status)


@router.get("/ingestion-tasks/{task_id}", response_model=KnowledgeIngestionTaskRead)
async def get_ingestion_task(
    task_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeIngestionTaskRead:
    """返回指定知识摄取任务。"""
    del current_user
    return _to_read(await _get_task(db, task_id))


@router.post("/ingestion-tasks/{task_id}/retry", response_model=KnowledgeIngestionTaskRead)
async def retry_ingestion_task(
    task_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeIngestionTaskRead:
    """重试失败或死亡的知识摄取任务。"""
    del current_user
    return await _reset_and_publish(db, task_id, reset_attempts=False)


@router.post("/ingestion-tasks/{task_id}/reexecute", response_model=KnowledgeIngestionTaskRead)
async def reexecute_ingestion_task(
    task_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeIngestionTaskRead:
    """允许管理员重新执行失败任务或已成功任务。"""
    del current_user
    return await _reset_and_publish(db, task_id, reset_attempts=True)


async def _reset_and_publish(db: AsyncSession, task_id: int, *, reset_attempts: bool) -> KnowledgeIngestionTaskRead:
    """兼容入口：重置并重新发布知识摄取任务。"""
    return await retry_ingestion_task_service(
        db,
        task_id,
        reset_attempts=reset_attempts,
    )


async def _get_task(db: AsyncSession, task_id: int) -> KnowledgeIngestionTask:
    """兼容入口：读取知识摄取任务。"""
    return await get_ingestion_task_service(db, task_id)


def _to_read(task: KnowledgeIngestionTask) -> KnowledgeIngestionTaskRead:
    """兼容入口：转换知识摄取任务响应模型。"""
    return ingestion_task_to_read(task)
