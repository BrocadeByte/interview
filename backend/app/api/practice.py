"""专项练习 HTTP 接口：只接收参数并注入用户、数据库依赖。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.interview import InterviewSessionRead
from app.schemas.practice import (
    PracticeComparisonRead,
    PracticeCreationRead,
    PracticeFromQuestionReviewCreate,
    PracticeFromReportCreate,
    PracticeListItem,
)
from app.services.practice_service import (
    create_question_practice_and_commit,
    create_report_practice_and_commit,
    get_comparison_and_commit,
    list_practices_and_commit,
    start_practice_and_commit,
    start_retest_and_commit,
)


router = APIRouter(prefix="/practice", tags=["practice"])


@router.post(
    "/from-report",
    response_model=PracticeCreationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_report(
    payload: PracticeFromReportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从报告薄弱项创建专项练习。"""
    return await create_report_practice_and_commit(
        db,
        user_id=current_user.id,
        payload=payload,
    )


@router.post(
    "/from-question-review",
    response_model=PracticeCreationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_question_review(
    payload: PracticeFromQuestionReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从指定逐题复盘创建专项练习。"""
    return await create_question_practice_and_commit(
        db,
        user_id=current_user.id,
        payload=payload,
    )


@router.get("", response_model=list[PracticeListItem])
async def list_practices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户的专项练习列表。"""
    return await list_practices_and_commit(db, current_user.id)


@router.post("/{practice_id}/start", response_model=InterviewSessionRead)
async def start_practice(
    practice_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """启动专项练习对应的面试会话。"""
    return await start_practice_and_commit(
        db,
        user_id=current_user.id,
        practice_id=practice_id,
    )


@router.post("/{practice_id}/start-retest", response_model=InterviewSessionRead)
async def start_retest(
    practice_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """为指定专项练习启动复测会话。"""
    return await start_retest_and_commit(
        db,
        user_id=current_user.id,
        practice_id=practice_id,
    )


@router.get("/{practice_id}/comparison", response_model=PracticeComparisonRead)
async def get_comparison(
    practice_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回专项练习前后的评分与薄弱项对比。"""
    return await get_comparison_and_commit(
        db,
        user_id=current_user.id,
        practice_id=practice_id,
    )
