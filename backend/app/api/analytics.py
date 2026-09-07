"""分析指标 HTTP 接口：负责权限与查询参数，统计逻辑位于 Service。"""

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import AnalyticsMetricsRead, ClientAnalyticsEventCreate
from app.services.analytics_service import (
    build_metrics_for_period,
    record_client_event_and_commit,
)


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def create_client_event(
    payload: ClientAnalyticsEventCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """接收服务端无法可靠推断的客户端交互事件。"""
    await record_client_event_and_commit(db, user_id=current_user.id, payload=payload)


@router.get("/metrics", response_model=AnalyticsMetricsRead)
async def get_metrics(
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsMetricsRead:
    """返回管理员指定区间或默认近 30 天的分析指标。"""
    del current_admin
    return await build_metrics_for_period(
        db,
        period_start=period_start,
        period_end=period_end,
    )
