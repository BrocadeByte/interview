"""岗位描述 HTTP 接口：注入用户与数据库后委托业务服务。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.job_description import JobDescription
from app.models.user import User
from app.schemas.job_description import JobDescriptionParse, JobDescriptionRead
from app.services.job_description_service import (
    activate_owned_job_description,
    get_owned_job_description as get_owned_job_description_service,
    list_user_job_descriptions,
    parse_and_save_job_description,
    parse_job_description_text,
    to_job_description_read,
)


router = APIRouter(prefix="/job-descriptions", tags=["job-descriptions"])


@router.post("/parse", response_model=JobDescriptionRead, status_code=status.HTTP_201_CREATED)
async def parse_job_description(
    payload: JobDescriptionParse,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDescriptionRead:
    """接收岗位原文并返回持久化后的结构化解析结果。"""
    return await parse_and_save_job_description(
        db,
        user_id=current_user.id,
        payload=payload,
        parse_text=parse_job_description_text,
    )


@router.get("", response_model=list[JobDescriptionRead])
async def list_job_descriptions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobDescriptionRead]:
    """返回当前用户保存的岗位描述列表。"""
    return await list_user_job_descriptions(db, current_user.id)


@router.get("/{job_description_id}", response_model=JobDescriptionRead)
async def get_job_description(
    job_description_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDescriptionRead:
    """返回当前用户拥有的指定岗位描述。"""
    return to_job_description_read(
        await get_owned_job_description(db, job_description_id, current_user.id)
    )


@router.post("/{job_description_id}/activate", response_model=JobDescriptionRead)
async def activate_job_description(
    job_description_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDescriptionRead:
    """把指定已解析岗位设置为当前唯一启用岗位。"""
    return await activate_owned_job_description(
        db,
        job_description_id=job_description_id,
        user_id=current_user.id,
    )


async def get_owned_job_description(
    db: AsyncSession,
    job_description_id: int,
    user_id: int,
) -> JobDescription:
    """兼容入口：读取当前用户拥有的岗位描述。"""
    return await get_owned_job_description_service(db, job_description_id, user_id)
