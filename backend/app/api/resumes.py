"""简历 HTTP 接口：负责接收文本/文件并把数据库会话注入简历服务。"""

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.profile import UserProfile
from app.models.resume import Resume
from app.models.user import User
from app.schemas.profile import ProfileRead
from app.schemas.resume import ResumePaste, ResumeRead
from app.services.resume_service import (
    RESUME_QUEUE_PUBLISH_ERROR,
    activate_owned_resume,
    apply_resume_profile,
    create_pasted_resume,
    create_uploaded_resume,
    get_owned_resume,
    list_user_resumes,
    persist_pending_resume,
    publish_resume_task_or_fail,
    record_resume_submission,
    to_resume_read,
)
from app.services.resume_queue import publish_resume_parse_task


router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.post("/paste", response_model=ResumeRead, status_code=status.HTTP_201_CREATED)
async def paste_resume(
    payload: ResumePaste,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeRead:
    """保存用户粘贴的简历并提交异步解析任务。"""
    return await create_pasted_resume(
        db,
        user_id=current_user.id,
        payload=payload,
        publish_task=publish_resume_parse_task,
    )


@router.post("/upload", response_model=ResumeRead, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeRead:
    """接收简历文件并提交异步解析任务。"""
    return await create_uploaded_resume(
        db,
        user_id=current_user.id,
        file=file,
        title=title,
        publish_task=publish_resume_parse_task,
    )


@router.get("", response_model=list[ResumeRead])
async def list_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ResumeRead]:
    """返回当前用户的简历列表与解析状态。"""
    return await list_user_resumes(db, current_user.id)


@router.get("/{resume_id}", response_model=ResumeRead)
async def get_resume(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeRead:
    """返回当前用户拥有的指定简历。"""
    return _to_resume_read(await _get_owned_resume(db, resume_id, current_user.id))


@router.post("/{resume_id}/activate", response_model=ResumeRead)
async def activate_resume(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeRead:
    """把指定已解析简历设为当前唯一启用简历。"""
    return await activate_owned_resume(
        db,
        resume_id=resume_id,
        user_id=current_user.id,
    )


@router.post("/{resume_id}/apply-profile", response_model=ProfileRead)
async def apply_resume_to_profile(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfile:
    """把已解析简历中的画像草稿应用到当前用户画像。"""
    return await apply_resume_profile(
        db,
        user_id=current_user.id,
        resume_id=resume_id,
    )


async def _persist_pending_resume(db: AsyncSession, resume: Resume) -> Resume:
    """兼容入口：持久化待解析简历。"""
    return await persist_pending_resume(db, resume)


async def _publish_resume_task_or_fail(db: AsyncSession, resume: Resume) -> None:
    """兼容入口：发布解析任务，失败时保存错误状态。"""
    await publish_resume_task_or_fail(db, resume, publish_resume_parse_task)


async def _record_resume_submission(db: AsyncSession, resume: Resume) -> None:
    """兼容入口：记录简历提交事件。"""
    await record_resume_submission(db, resume)


async def _get_owned_resume(db: AsyncSession, resume_id: int, user_id: int) -> Resume:
    """兼容入口：读取用户拥有的简历。"""
    return await get_owned_resume(db, resume_id, user_id)


def _to_resume_read(resume: Resume) -> ResumeRead:
    """兼容入口：转换简历响应模型。"""
    return to_resume_read(resume)
