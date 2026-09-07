"""求职画像 HTTP 接口：负责依赖注入，画像业务统一交给 Service。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.job_description import JobDescription
from app.models.profile import UserProfile
from app.models.resume import Resume
from app.models.user import User
from app.schemas.profile import AutoProfileDraft, AutoProfileGenerate, ProfileRead, ProfileUpdate
from app.services.profile_service import (
    generate_auto_profile_draft,
    get_or_create_profile,
    get_owned_job_description,
    get_owned_resume,
    get_user_profile,
    update_user_profile,
)


router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("/auto-generate", response_model=AutoProfileDraft)
async def auto_generate_profile(
    payload: AutoProfileGenerate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AutoProfileDraft:
    """生成供用户确认的画像草稿，不直接覆盖已有画像字段。"""
    return await generate_auto_profile_draft(
        db,
        user_id=current_user.id,
        payload=payload,
    )


# 获取当前登录用户的求职画像；如果不存在则自动创建空画像。
@router.get("/me", response_model=ProfileRead)
async def get_profile(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> UserProfile:
    """获取当前用户画像；首次访问时由 Service 创建空画像。"""
    return await get_or_create_profile(db, current_user.id)


# 更新当前登录用户的求职画像信息。
@router.put("/me", response_model=ProfileRead)
async def update_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfile:
    """更新请求中显式提供的画像字段。"""
    return await update_user_profile(db, user_id=current_user.id, payload=payload)


# 按用户 ID 查询求职画像。
async def _get_user_profile(db: AsyncSession, user_id: int) -> UserProfile | None:
    """兼容入口：按用户 ID 查询画像。"""
    return await get_user_profile(db, user_id)


async def _get_owned_resume(db: AsyncSession, resume_id: int, user_id: int) -> Resume:
    """兼容入口：读取用户拥有的简历。"""
    return await get_owned_resume(db, resume_id, user_id)


async def _get_owned_job_description(
    db: AsyncSession,
    job_description_id: int,
    user_id: int,
) -> JobDescription:
    """兼容入口：读取用户拥有的岗位描述。"""
    return await get_owned_job_description(db, job_description_id, user_id)
