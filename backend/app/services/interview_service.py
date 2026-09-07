"""面试会话业务编排。

本模块负责面试会话的查询、事务和 Agent 调度。API 层只需要把 FastAPI 注入的
数据库会话与当前用户信息传入这里，不直接拼装 SQL 或推进面试状态。
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.graph import interview_graph
from app.agents.nodes.interview_planner import (
    get_interview_question_count,
    get_plan_item_for_question,
)
from app.models.interview import InterviewMessage, InterviewSession
from app.models.job_description import JobDescription
from app.models.profile import UserProfile
from app.models.report import InterviewReport
from app.models.resume import Resume
from app.schemas.interview import InterviewCreate
from app.schemas.llm_outputs import VisibleQuestionOutput
from app.schemas.question_review import QuestionReviewRead
from app.schemas.report import InterviewReportRead
from app.schemas.score import InterviewScoreRead
from app.services.analytics_service import record_analytics_event_safely
from app.services.interview_answer_service import (
    SessionLeaseHeartbeat,
    acquire_answer_lease,
    release_answer_lease,
)
from app.services.interview_memory_service import maybe_compact_medium_term_memory
from app.services.interview_state_service import build_state_from_session
from app.services.job_description_service import build_job_description_snapshot
from app.services.practice_service import attach_practice_fields, sync_practice_for_interview
from app.services.question_review_service import ensure_question_reviews
from app.services.report_service import get_or_create_report, is_final_report_session
from app.services.resume_service import build_resume_snapshot
from app.services.score_service import list_scores, save_latest_score
from app.rag.embeddings import embed_text
from app.rag.retriever import ensure_collection


logger = logging.getLogger(__name__)

# 允许 API 层在测试中注入压缩函数，同时保持生产代码使用默认实现。
MemoryCompactor = Callable[[AsyncSession, InterviewSession], Awaitable[None]]


def planner_knowledge_query(target_position: str) -> str:
    """构造面试计划预热使用的知识库检索语句。"""
    return f"{target_position.strip()} 面试计划 岗位能力模型 评分标准"


async def warmup_interview_dependencies(target_position: str) -> None:
    """并行预热向量库与目标岗位的查询向量；失败不影响正式面试。"""
    try:
        await asyncio.gather(
            ensure_collection(),
            embed_text(planner_knowledge_query(target_position)),
        )
    except Exception as exc:
        logger.warning(
            "interview.warmup.failed target_position=%r error=%r",
            target_position,
            exc,
        )


async def create_interview_session(
    db: AsyncSession,
    *,
    user_id: int,
    payload: InterviewCreate,
) -> InterviewSession:
    """创建待启动会话，并冻结本次面试使用的简历和岗位快照。"""
    resume = await _get_interview_resume(db, user_id, payload.resume_id)
    job_description = await _get_interview_job_description(
        db,
        user_id,
        payload.job_description_id,
    )
    await _validate_parent_session(db, user_id, payload.parent_session_id)
    await _validate_source_report(db, user_id, payload.source_report_id)

    session = InterviewSession(
        user_id=user_id,
        target_position=payload.target_position,
        difficulty=payload.difficulty,
        mode=payload.mode,
        interview_type=payload.interview_type,
        status="preparing",
        resume_id=resume.id if resume else None,
        resume_snapshot_json=build_resume_snapshot(resume) if resume else None,
        job_description_id=job_description.id if job_description else None,
        job_description_snapshot_json=(
            build_job_description_snapshot(job_description) if job_description else None
        ),
        parent_session_id=payload.parent_session_id,
        source_report_id=payload.source_report_id,
        source_weakness_key=payload.source_weakness_key,
        session_purpose=payload.session_purpose,
        comparison_group_id=payload.comparison_group_id,
    )
    db.add(session)
    await db.flush()
    await record_analytics_event_safely(
        db,
        event_name="interview_created",
        user_id=user_id,
        session_id=session.id,
        resume_id=session.resume_id,
        job_description_id=session.job_description_id,
        deduplication_key=f"interview_created:{session.id}",
        properties={
            "mode": session.mode,
            "interview_type": session.interview_type,
            "session_purpose": session.session_purpose,
        },
    )
    await db.commit()
    return await load_interview_session(db, user_id, session.id)


async def list_interview_sessions(db: AsyncSession, user_id: int) -> list[InterviewSession]:
    """按最近更新时间倒序返回当前用户的面试会话。"""
    result = await db.scalars(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id)
        .order_by(InterviewSession.updated_at.desc())
    )
    return list(result)


async def resume_started_session(
    db: AsyncSession,
    session: InterviewSession,
) -> InterviewSession:
    """幂等恢复已生成首题的会话，并确保开始事件和活动状态已提交。"""
    if session.status == "preparing":
        session.status = "active"
    await record_interview_started(db, session)
    await db.commit()
    return await load_interview_session(db, session.user_id, session.id)


async def get_interview_scores(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: int,
) -> list[InterviewScoreRead]:
    """校验会话归属后读取全部逐题评分。"""
    await load_interview_session(db, user_id, session_id)
    return await list_scores(db, session_id)


async def get_interview_question_reviews(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: int,
) -> list[QuestionReviewRead]:
    """为已完成的正式面试生成或读取稳定的逐题复盘。"""
    session = await load_interview_session(db, user_id, session_id)
    if not is_final_report_session(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Question reviews are available after the full interview is finished",
        )
    report_read = await get_or_create_report(db, session_id)
    if report_read.id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Final report is not available",
        )
    report = await db.get(InterviewReport, report_read.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    reviews = await ensure_question_reviews(db, report)
    await db.commit()
    return reviews


async def get_interview_report(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: int,
) -> InterviewReportRead:
    """读取或生成面试报告，并记录最终报告的首次查看事件。"""
    await load_interview_session(db, user_id, session_id)
    report = await get_or_create_report(db, session_id)
    if report.id is not None and report.is_final:
        await record_analytics_event_safely(
            db,
            event_name="report_viewed",
            user_id=user_id,
            session_id=session_id,
            report_id=report.id,
            deduplication_key=f"report_viewed:{user_id}:{report.id}",
        )
    await db.commit()
    return report


async def process_answer(
    db: AsyncSession,
    session: InterviewSession,
    *,
    user_id: int,
    answer: str,
    request_id: str,
    compact_memory: MemoryCompactor = maybe_compact_medium_term_memory,
) -> InterviewSession:
    """执行一轮回答事务：保存回答、运行面试图、落分并推进会话。

    追问仍归属当前主问题；只有进入下一道主问题时才增加题号。租约释放、消息、
    评分和会话状态在同一次提交中完成，避免客户端读到半完成状态。
    """
    existing_followups = _count_current_followups(session)
    current_dimension = _current_question_dimension(session)

    db.add(
        InterviewMessage(
            session_id=session.id,
            role="user",
            content=answer,
            request_id=request_id,
            question_index=session.current_question_index,
            dimension=current_dimension,
            is_followup=1 if existing_followups > 0 else 0,
            followup_index=existing_followups,
        )
    )
    await db.flush()

    # 长对话先压缩旧消息，再重载会话，确保 Agent 看到最新的记忆与回答。
    session = await load_interview_session(db, user_id, session.id)
    await compact_memory(db, session)
    session = await load_interview_session(db, user_id, session.id)
    profile = await get_user_profile(db, user_id)
    state = build_state_from_session(session, profile)
    state["action"] = "answer"
    result = await interview_graph.ainvoke(state)

    await save_latest_score(db, session.id, result["scores"])
    question_state = _advance_after_answer(session, result)
    question = validated_visible_question(result["current_question"])

    # 模型偶尔会重复上一题；此处使用确定性兜底问题，避免面试卡住。
    if is_duplicate_question(question, session.messages):
        question, question_state = _replace_duplicate_question(
            session,
            question_state,
        )

    db.add(
        InterviewMessage(
            session_id=session.id,
            role="assistant",
            content=question,
            question_index=question_state["question_index"],
            dimension=result.get("current_dimension") or current_dimension,
            is_followup=question_state["is_followup"],
            followup_index=question_state["followup_index"],
        )
    )
    await sync_practice_for_interview(db, session)
    if session.status == "finished":
        await record_interview_finished(db, session)
    await release_answer_lease(
        db,
        session_id=session.id,
        request_id=request_id,
        commit=False,
        require_held=True,
    )
    await db.commit()
    return await load_interview_session(db, user_id, session.id)


async def finish_interview_session(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: int,
    request_id: str,
) -> InterviewSession:
    """主动结束面试，并与正在处理的回答使用同一把会话租约互斥。"""
    session = await load_interview_session(db, user_id, session_id)
    if session.status == "finished":
        await record_interview_finished(db, session)
        await db.commit()
        return session

    acquired = await acquire_answer_lease(
        db,
        session_id=session_id,
        user_id=user_id,
        request_id=request_id,
        allowed_statuses=("preparing", "active"),
    )
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An answer is being processed; try finishing again shortly",
        )

    try:
        session = await load_interview_session(db, user_id, session_id)
        profile = await get_user_profile(db, user_id)
        state = build_state_from_session(session, profile)
        state["action"] = "finish"
        result = await interview_graph.ainvoke(state)

        session.status = result["status"]
        db.add(
            InterviewMessage(
                session_id=session.id,
                role="assistant",
                content=validated_visible_question(result["current_question"]),
                question_index=session.current_question_index,
                dimension=result.get("current_dimension"),
                is_followup=0,
                followup_index=0,
            )
        )
        await sync_practice_for_interview(db, session)
        await record_interview_finished(db, session)
        await release_answer_lease(
            db,
            session_id=session.id,
            request_id=request_id,
            commit=False,
            require_held=True,
        )
        await db.commit()
        return await load_interview_session(db, user_id, session.id)
    except Exception:
        await db.rollback()
        await release_answer_lease(db, session_id=session_id, request_id=request_id)
        raise


async def process_start(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: int,
    request_id: str,
    heartbeat: SessionLeaseHeartbeat,
) -> InterviewSession:
    """在可续租、带所有权校验的会话租约内生成并提交首题。"""
    heartbeat.start()
    try:
        session = await load_interview_session(db, user_id, session_id)
        profile = await get_user_profile(db, user_id)
        state = build_state_from_session(session, profile, messages=[])
        state["action"] = "start"
        result = await interview_graph.ainvoke(state)

        session.interview_plan_json = json.dumps(
            result.get("interview_plan") or [],
            ensure_ascii=False,
        )
        session.status = "active"
        await sync_practice_for_interview(db, session)
        await record_interview_started(db, session)
        db.add(
            InterviewMessage(
                session_id=session.id,
                role="assistant",
                content=validated_visible_question(result["current_question"]),
                question_index=session.current_question_index,
                dimension=result.get("current_dimension"),
                is_followup=0,
                followup_index=0,
            )
        )

        # 提交前停止续租并确认租约仍属于当前请求，防止过期任务写入结果。
        await heartbeat.stop()
        if heartbeat.lost:
            from app.services.interview_answer_service import SessionLeaseLostError

            raise SessionLeaseLostError("Interview session lease ownership was lost")
        await release_answer_lease(
            db,
            session_id=session_id,
            request_id=request_id,
            commit=False,
            require_held=True,
        )
        await db.commit()
        return await load_interview_session(db, user_id, session_id)
    except BaseException:
        await heartbeat.stop()
        await db.rollback()
        await release_answer_lease(db, session_id=session_id, request_id=request_id)
        raise


async def load_interview_session(
    db: AsyncSession,
    user_id: int,
    session_id: int,
) -> InterviewSession:
    """加载用户拥有的会话、消息和记忆，并补充响应需要的派生字段。"""
    session = await db.scalar(
        select(InterviewSession)
        .options(
            selectinload(InterviewSession.messages),
            selectinload(InterviewSession.memories),
        )
        .where(
            InterviewSession.id == session_id,
            InterviewSession.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found")
    attach_current_plan_fields(session)
    await attach_practice_fields(db, session)
    return session


async def record_interview_started(db: AsyncSession, session: InterviewSession) -> None:
    """幂等记录面试开始事件。"""
    await record_analytics_event_safely(
        db,
        event_name="interview_started",
        user_id=session.user_id,
        session_id=session.id,
        report_id=session.source_report_id,
        practice_id=getattr(session, "practice_id", None),
        deduplication_key=f"interview_started:{session.id}",
        properties={
            "mode": session.mode,
            "interview_type": session.interview_type,
            "session_purpose": session.session_purpose,
        },
    )


async def record_interview_finished(db: AsyncSession, session: InterviewSession) -> None:
    """幂等记录面试结束事件，并区分专项练习与复测。"""
    if session.status != "finished":
        return
    practice_id = getattr(session, "practice_id", None)
    await record_analytics_event_safely(
        db,
        event_name="interview_finished",
        user_id=session.user_id,
        session_id=session.id,
        report_id=session.source_report_id,
        practice_id=practice_id,
        deduplication_key=f"interview_finished:{session.id}",
        properties={"session_purpose": session.session_purpose},
    )
    if session.session_purpose == "weakness_practice":
        event_name = "practice_finished"
    elif session.session_purpose == "retest":
        event_name = "retest_finished"
    else:
        return
    await record_analytics_event_safely(
        db,
        event_name=event_name,
        user_id=session.user_id,
        session_id=session.id,
        report_id=session.source_report_id,
        practice_id=practice_id,
        deduplication_key=f"{event_name}:{session.id}",
    )


async def get_user_profile(db: AsyncSession, user_id: int) -> UserProfile | None:
    """查询用户画像；画像缺失时允许 Agent 使用默认上下文。"""
    return await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))


def parse_interview_plan(plan_json: str | None) -> list[dict]:
    """安全解析持久化的面试计划，非法或非列表 JSON 统一视为空计划。"""
    try:
        plan = json.loads(plan_json or "[]")
    except json.JSONDecodeError:
        return []
    return plan if isinstance(plan, list) else []


def attach_current_plan_fields(session: InterviewSession) -> None:
    """根据持久化计划补充仅供接口响应使用的当前计划字段。"""
    plan = parse_interview_plan(session.interview_plan_json)
    plan_item = get_plan_item_for_question(plan, session.current_question_index)
    setattr(session, "current_dimension", plan_item.get("dimension"))
    setattr(session, "current_plan_focus", plan_item.get("focus"))
    setattr(session, "total_question_count", get_interview_question_count(plan))


def validated_visible_question(value: object) -> str:
    """使用统一输出模型清洗并校验最终展示给用户的问题。"""
    return VisibleQuestionOutput.model_validate({"question": value}).question


def is_duplicate_question(question: str, messages: list[InterviewMessage]) -> bool:
    """忽略空白和大小写，判断问题是否已在当前会话中出现。"""
    normalized = "".join(question.split()).casefold()
    return any(
        message.role == "assistant"
        and "".join(message.content.split()).casefold() == normalized
        for message in messages
    )


async def _get_interview_resume(
    db: AsyncSession,
    user_id: int,
    resume_id: int | None,
) -> Resume | None:
    """读取当前用户指定的简历，并确保简历已完成解析后才能用于面试。"""
    if resume_id is None:
        return None
    resume = await db.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    if resume.status != "parsed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a parsed resume can be used for an interview",
        )
    return resume


async def _get_interview_job_description(
    db: AsyncSession,
    user_id: int,
    job_description_id: int | None,
) -> JobDescription | None:
    """读取当前用户指定的职位描述，并确保已解析后才能用于面试。"""
    if job_description_id is None:
        return None
    job_description = await db.scalar(
        select(JobDescription).where(
            JobDescription.id == job_description_id,
            JobDescription.user_id == user_id,
        )
    )
    if job_description is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job description not found",
        )
    if job_description.status != "parsed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a parsed job description can be used for an interview",
        )
    return job_description


async def _validate_parent_session(
    db: AsyncSession,
    user_id: int,
    parent_session_id: int | None,
) -> None:
    """校验练习来源面试归当前用户所有，未指定来源时直接返回。"""
    if parent_session_id is None:
        return
    parent_exists = await db.scalar(
        select(InterviewSession.id).where(
            InterviewSession.id == parent_session_id,
            InterviewSession.user_id == user_id,
        )
    )
    if parent_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent interview not found",
        )


async def _validate_source_report(
    db: AsyncSession,
    user_id: int,
    source_report_id: int | None,
) -> None:
    """校验来源报告属于当前用户已完成的完整面试，且为正式报告。"""
    if source_report_id is None:
        return
    source_report_exists = await db.scalar(
        select(InterviewReport.id)
        .join(InterviewSession, InterviewSession.id == InterviewReport.session_id)
        .where(
            InterviewReport.id == source_report_id,
            InterviewReport.is_final.is_(True),
            InterviewSession.user_id == user_id,
            InterviewSession.status == "finished",
            InterviewSession.session_purpose == "full_interview",
        )
    )
    if source_report_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source report not found",
        )


def _count_current_followups(session: InterviewSession) -> int:
    """统计当前主问题下已经保存的面试官追问数量。"""
    return sum(
        1
        for message in session.messages
        if message.role == "assistant"
        and message.question_index == session.current_question_index
        and bool(message.is_followup)
    )


def _current_question_dimension(session: InterviewSession) -> str:
    """优先读取当前题目消息中持久化的维度，缺失时按面试计划推导。"""
    current_plan_item = get_plan_item_for_question(
        parse_interview_plan(session.interview_plan_json),
        session.current_question_index,
    )
    return next(
        (
            message.dimension
            for message in reversed(session.messages)
            if message.role == "assistant"
            and message.question_index == session.current_question_index
            and message.dimension
        ),
        current_plan_item["dimension"],
    )


def _advance_after_answer(session: InterviewSession, result: dict) -> dict[str, int]:
    """根据 Agent 的追问决策推进题号，并返回待保存消息的定位信息。"""
    if result["followup_decision"] and result["followup_decision"]["needs_followup"]:
        return {
            "is_followup": 1,
            "followup_index": result["follow_up_count"],
            "question_index": session.current_question_index,
        }
    if result["status"] == "finished":
        session.status = "finished"
    else:
        session.current_question_index += 1
    return {
        "is_followup": 0,
        "followup_index": 0,
        "question_index": session.current_question_index,
    }


def _replace_duplicate_question(
    session: InterviewSession,
    question_state: dict[str, int],
) -> tuple[str, dict[str, int]]:
    """用确定性问题替换重复题，并在必要时结束已到题目上限的面试。"""
    plan = parse_interview_plan(session.interview_plan_json)
    total_question_count = get_interview_question_count(plan)
    is_followup = bool(question_state["is_followup"])

    if is_followup and session.current_question_index >= total_question_count:
        session.status = "finished"
        question = validated_visible_question("本次模拟面试已完成。可以查看评分与复盘报告。")
    else:
        if is_followup:
            session.current_question_index += 1
        next_plan_item = get_plan_item_for_question(plan, session.current_question_index)
        question = validated_visible_question(
            f"请围绕{next_plan_item['dimension']}，结合一个尚未讨论的具体经历说明你的做法、取舍和结果。"
        )

    return question, {
        "is_followup": 0,
        "followup_index": 0,
        "question_index": session.current_question_index,
    }
