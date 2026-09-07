"""面试报告 HTTP 接口：注入用户与数据库后委托报告服务。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.question_review import QuestionReviewRead
from app.schemas.report import InterviewReportListItem, InterviewReportRead
from app.services.report_service import (
    get_owned_report,
    get_owned_report_question_reviews,
    list_owned_reports,
)


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{report_id}/question-reviews", response_model=list[QuestionReviewRead])
async def get_report_question_reviews(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[QuestionReviewRead]:
    """返回当前用户最终报告对应的逐题复盘快照。"""
    return await get_owned_report_question_reviews(
        db,
        report_id=report_id,
        user_id=current_user.id,
    )


# 获取当前登录用户的所有面试报告列表。
@router.get("", response_model=list[InterviewReportListItem])
async def list_reports(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[InterviewReportListItem]:
    """返回当前用户的最终面试报告列表。"""
    return await list_owned_reports(db, current_user.id)


# 获取指定报告详情，并校验报告归属当前用户。
@router.get("/{report_id}", response_model=InterviewReportRead)
async def get_report(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewReportRead:
    """返回当前用户拥有的报告详情并记录查看事件。"""
    return await get_owned_report(
        db,
        user_id=current_user.id,
        report_id=report_id,
    )
