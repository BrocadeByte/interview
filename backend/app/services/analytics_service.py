import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent
from app.models.interview import InterviewSession
from app.models.job_description import JobDescription
from app.models.question_review import QuestionReview
from app.models.report import InterviewReport
from app.models.resume import Resume
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsEventName,
    AnalyticsMetricsRead,
    ClientAnalyticsEventCreate,
    ConversionMetric,
    FirstExperienceFunnel,
)


logger = logging.getLogger(__name__)


async def record_client_event_and_commit(
    db: AsyncSession,
    *,
    user_id: int,
    payload: ClientAnalyticsEventCreate,
) -> None:
    """记录只能由浏览器感知的交互事件，并提交去重后的结果。"""
    await record_client_event(db, user_id=user_id, payload=payload)
    await db.commit()


async def build_metrics_for_period(
    db: AsyncSession,
    *,
    period_start: date | None,
    period_end: date | None,
) -> AnalyticsMetricsRead:
    """补齐默认 30 天统计区间并生成管理端指标。"""
    today = datetime.utcnow().date()
    resolved_end = period_end or today
    resolved_start = period_start or (resolved_end - timedelta(days=29))
    return await build_analytics_metrics(
        db,
        period_start=resolved_start,
        period_end=resolved_end,
    )


async def record_analytics_event(
    db: AsyncSession,
    *,
    event_name: AnalyticsEventName,
    user_id: int,
    deduplication_key: str | None = None,
    session_id: int | None = None,
    report_id: int | None = None,
    practice_id: int | None = None,
    question_review_id: int | None = None,
    resume_id: int | None = None,
    job_description_id: int | None = None,
    properties: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """写入带业务去重键的分析事件。"""
    """Insert an event without turning retries into duplicate rows."""
    values = {
        "event_name": event_name,
        "user_id": user_id,
        "deduplication_key": deduplication_key or f"server:{event_name}:{uuid4()}",
        "session_id": session_id,
        "report_id": report_id,
        "practice_id": practice_id,
        "question_review_id": question_review_id,
        "resume_id": resume_id,
        "job_description_id": job_description_id,
        "properties_json": json.dumps(properties or {}, ensure_ascii=False, separators=(",", ":")),
        "occurred_at": occurred_at or datetime.utcnow(),
    }
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "mysql":
        statement = mysql_insert(AnalyticsEvent).values(**values).prefix_with("IGNORE")
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(AnalyticsEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["deduplication_key"])
        )
    else:
        existing = await db.scalar(
            select(AnalyticsEvent.id).where(
                AnalyticsEvent.deduplication_key == values["deduplication_key"]
            )
        )
        if existing is not None:
            return
        db.add(AnalyticsEvent(**values))
        return
    await db.execute(statement)


async def record_analytics_event_safely(db: AsyncSession, **kwargs: Any) -> None:
    """隔离埋点记录失败，避免因此回滚用户的主要业务操作。"""
    try:
        async with db.begin_nested():
            await record_analytics_event(db, **kwargs)
    except SQLAlchemyError as exc:
        logger.warning(
            "analytics.event.failed event_name=%s user_id=%s error=%r",
            kwargs.get("event_name"),
            kwargs.get("user_id"),
            exc,
        )


async def record_client_event(
    db: AsyncSession,
    *,
    user_id: int,
    payload: ClientAnalyticsEventCreate,
) -> None:
    """校验事件关联资源归当前用户所有，再使用客户端事件标识记录可去重的事件。"""
    if payload.resume_id is not None:
        await _require_owned_resume(db, user_id, payload.resume_id)
    if payload.job_description_id is not None:
        await _require_owned_job_description(db, user_id, payload.job_description_id)
    if payload.event_name == "question_review_expanded":
        await _require_owned_question_review(
            db,
            user_id,
            payload.report_id,
            payload.question_review_id,
        )

    await record_analytics_event(
        db,
        event_name=payload.event_name,
        user_id=user_id,
        deduplication_key=_client_deduplication_key(user_id, payload.client_event_id),
        report_id=payload.report_id,
        question_review_id=payload.question_review_id,
        resume_id=payload.resume_id,
        job_description_id=payload.job_description_id,
    )


async def build_analytics_metrics(
    db: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    now: datetime | None = None,
) -> AnalyticsMetricsRead:
    """根据稳定的产品数据计算 BE-B2C-009 定义的三项转化指标。"""
    if period_end < period_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_end must be on or after period_start",
        )
    start_at = datetime.combine(period_start, time.min)
    end_at = datetime.combine(period_end + timedelta(days=1), time.min)
    generated_at = now or datetime.utcnow()

    cohort_user_ids = set(
        await db.scalars(
            select(User.id).where(User.created_at >= start_at, User.created_at < end_at)
        )
    )
    period_events = list(
        await db.scalars(
            select(AnalyticsEvent).where(
                AnalyticsEvent.occurred_at >= start_at,
                AnalyticsEvent.occurred_at < end_at,
            )
        )
    )
    event_counts = Counter(event.event_name for event in period_events)

    cohort_events = defaultdict(set)
    for event in period_events:
        if event.user_id in cohort_user_ids:
            cohort_events[event.event_name].add(event.user_id)

    period_session_ids = {
        event.session_id for event in period_events if event.session_id is not None
    }
    period_session_purposes = {
        session_id: purpose
        for session_id, purpose in (
            await db.execute(
                select(InterviewSession.id, InterviewSession.session_purpose).where(
                    InterviewSession.id.in_(period_session_ids)
                )
            )
        ).all()
    } if period_session_ids else {}
    first_started_users = {
        event.user_id
        for event in period_events
        if event.event_name == "interview_started"
        and event.user_id in cohort_user_ids
        and event.session_id is not None
        and period_session_purposes.get(event.session_id) == "full_interview"
    }
    resume_users = cohort_events["resume_uploaded"] | cohort_events["resume_pasted"]
    funnel = FirstExperienceFunnel(
        registered_users=len(cohort_user_ids),
        resume_provided_users=len(resume_users),
        jd_pasted_users=len(cohort_events["jd_pasted"]),
        profile_auto_generated_users=len(cohort_events["profile_auto_generated"]),
        profile_applied_users=len(cohort_events["profile_applied"]),
        interview_created_users=len(cohort_events["interview_created"]),
        first_interview_started_users=len(first_started_users),
    )

    report_views: dict[tuple[int, int], datetime] = {}
    practice_creations: dict[tuple[int, int], list[datetime]] = defaultdict(list)
    for event in period_events:
        if event.report_id is None:
            continue
        key = (event.user_id, event.report_id)
        if event.event_name == "report_viewed":
            current = report_views.get(key)
            if current is None or event.occurred_at < current:
                report_views[key] = event.occurred_at
        elif event.event_name == "practice_created":
            practice_creations[key].append(event.occurred_at)
    practiced_reports = {
        key
        for key, viewed_at in report_views.items()
        if any(created_at >= viewed_at for created_at in practice_creations.get(key, []))
    }

    first_full_finishes = {
        user_id: finished_at
        for user_id, finished_at in (
            await db.execute(
                select(
                    AnalyticsEvent.user_id,
                    func.min(AnalyticsEvent.occurred_at),
                )
                .join(
                    InterviewSession,
                    InterviewSession.id == AnalyticsEvent.session_id,
                )
                .where(
                    AnalyticsEvent.event_name == "interview_finished",
                    AnalyticsEvent.occurred_at < end_at,
                    InterviewSession.session_purpose == "full_interview",
                )
                .group_by(AnalyticsEvent.user_id)
            )
        ).all()
    }

    maturity_cutoff = generated_at - timedelta(days=7)
    eligible_finishes = {
        user_id: finished_at
        for user_id, finished_at in first_full_finishes.items()
        if start_at <= finished_at < end_at and finished_at <= maturity_cutoff
    }
    repracticed_users: set[int] = set()
    if eligible_finishes:
        earliest_finish = min(eligible_finishes.values())
        latest_observation = max(eligible_finishes.values()) + timedelta(days=7)
        started_events = list(
            await db.scalars(
                select(AnalyticsEvent)
                .join(
                    InterviewSession,
                    InterviewSession.id == AnalyticsEvent.session_id,
                )
                .where(
                    AnalyticsEvent.event_name == "interview_started",
                    AnalyticsEvent.user_id.in_(eligible_finishes),
                    AnalyticsEvent.occurred_at > earliest_finish,
                    AnalyticsEvent.occurred_at <= latest_observation,
                    InterviewSession.session_purpose.in_(
                        ["full_interview", "weakness_practice"]
                    ),
                )
            )
        )
        for event in started_events:
            finished_at = eligible_finishes[event.user_id]
            if finished_at < event.occurred_at <= finished_at + timedelta(days=7):
                repracticed_users.add(event.user_id)

    return AnalyticsMetricsRead(
        period_start=period_start,
        period_end=period_end,
        first_experience_funnel=funnel,
        registration_to_first_interview_start=_conversion(
            len(cohort_user_ids), len(first_started_users)
        ),
        report_to_practice=_conversion(len(report_views), len(practiced_reports)),
        seven_day_repractice=_conversion(
            len(eligible_finishes), len(repracticed_users)
        ),
        event_counts=dict(sorted(event_counts.items())),
        definitions={
            "registration_to_first_interview_start": (
                "统计期内注册用户中，在同期首次启动完整面试的去重用户占比"
            ),
            "report_to_practice": (
                "统计期内首次被查看的最终报告中，查看后创建专项练习的去重报告占比"
            ),
            "seven_day_repractice": (
                "统计期内首次完成完整面试且已满 7 天观察窗的用户中，7 天内再次启动完整面试或专项练习的占比；再测不计为复练"
            ),
        },
        generated_at=generated_at,
    )


def _conversion(denominator: int, numerator: int) -> ConversionMetric:
    """根据分子和分母计算四位小数的转化率，分母为零时返回零。"""
    return ConversionMetric(
        denominator=denominator,
        numerator=numerator,
        rate=round(numerator / denominator, 4) if denominator else 0,
    )


def _client_deduplication_key(user_id: int, client_event_id: UUID) -> str:
    """组合用户标识与客户端事件 UUID，生成客户端埋点的去重键。"""
    return f"client:{user_id}:{client_event_id}"


async def _require_owned_resume(db: AsyncSession, user_id: int, resume_id: int) -> None:
    """确认简历属于当前用户，否则返回资源不存在错误。"""
    exists = await db.scalar(
        select(Resume.id).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")


async def _require_owned_job_description(
    db: AsyncSession, user_id: int, job_description_id: int
) -> None:
    """确认职位描述属于当前用户，否则返回资源不存在错误。"""
    exists = await db.scalar(
        select(JobDescription.id).where(
            JobDescription.id == job_description_id,
            JobDescription.user_id == user_id,
        )
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job description not found",
        )


async def _require_owned_question_review(
    db: AsyncSession,
    user_id: int,
    report_id: int | None,
    question_review_id: int | None,
) -> None:
    """确认逐题复盘属于当前用户的指定正式报告，否则返回资源不存在错误。"""
    exists = await db.scalar(
        select(QuestionReview.id)
        .join(InterviewReport, InterviewReport.id == QuestionReview.report_id)
        .join(InterviewSession, InterviewSession.id == InterviewReport.session_id)
        .where(
            QuestionReview.id == question_review_id,
            QuestionReview.report_id == report_id,
            InterviewReport.is_final.is_(True),
            InterviewSession.user_id == user_id,
        )
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question review not found",
        )
