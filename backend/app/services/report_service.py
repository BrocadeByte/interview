import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nodes.interview_planner import get_plan_item_for_question
from app.agents.nodes.report_generator import fallback_report, generate_report, report_content_is_incomplete
from app.models.interview import InterviewMessage, InterviewSession
from app.models.report import InterviewReport
from app.schemas.question_review import QuestionReviewRead
from app.schemas.report import InterviewReportListItem, InterviewReportRead
from app.services.analytics_service import record_analytics_event_safely
from app.services.citation_service import citations_from_json, citations_to_json, merge_citations
from app.services.question_review_service import ensure_question_reviews
from app.services.score_service import list_scores


async def get_owned_report_question_reviews(
    db: AsyncSession,
    *,
    report_id: int,
    user_id: int,
) -> list[QuestionReviewRead]:
    """校验最终报告归属后返回稳定的逐题复盘快照。"""
    result = (
        await db.execute(_owned_final_report_query(report_id, user_id))
    ).first()
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    report, _session = result
    reviews = await ensure_question_reviews(db, report)
    await db.commit()
    return reviews


async def list_owned_reports(
    db: AsyncSession,
    user_id: int,
) -> list[InterviewReportListItem]:
    """按生成时间倒序返回用户已完成正式面试的最终报告。"""
    rows = await db.execute(
        select(InterviewReport, InterviewSession)
        .join(InterviewSession, InterviewReport.session_id == InterviewSession.id)
        .where(
            InterviewSession.user_id == user_id,
            InterviewSession.status == "finished",
            InterviewSession.session_purpose == "full_interview",
            InterviewReport.is_final.is_(True),
        )
        .order_by(InterviewReport.created_at.desc())
    )
    return [
        InterviewReportListItem(
            id=report.id,
            session_id=report.session_id,
            target_position=session.target_position,
            difficulty=session.difficulty,
            total_score=report.total_score,
            created_at=report.created_at,
            updated_at=report.updated_at,
        )
        for report, session in rows.all()
    ]


async def get_owned_report(
    db: AsyncSession,
    *,
    report_id: int,
    user_id: int,
) -> InterviewReportRead:
    """读取最终报告，修复旧的不完整数据并记录查看事件。"""
    result = (
        await db.execute(_owned_final_report_query(report_id, user_id))
    ).first()
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    report, session = result
    if await repair_report_if_incomplete(db, report, session):
        await db.flush()
    await ensure_question_reviews(db, report)
    await record_analytics_event_safely(
        db,
        event_name="report_viewed",
        user_id=user_id,
        session_id=session.id,
        report_id=report.id,
        deduplication_key=f"report_viewed:{user_id}:{report.id}",
    )
    await db.commit()
    return report_to_read(report)


def _owned_final_report_query(report_id: int, user_id: int):
    """构造最终报告归属查询，供详情与逐题复盘共享同一套边界。"""
    return (
        select(InterviewReport, InterviewSession)
        .join(InterviewSession, InterviewReport.session_id == InterviewSession.id)
        .where(
            InterviewReport.id == report_id,
            InterviewReport.is_final.is_(True),
            InterviewSession.user_id == user_id,
            InterviewSession.status == "finished",
            InterviewSession.session_purpose == "full_interview",
        )
    )


async def get_or_create_report(db: AsyncSession, session_id: int) -> InterviewReportRead:
    """进行中会话使用确定性预览；完整面试结束后才调用模型并保存最终快照。"""
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise ValueError(f"Interview session {session_id} does not exist")

    existing = await db.scalar(select(InterviewReport).where(InterviewReport.session_id == session_id))
    if existing and bool(existing.is_final) and is_final_report_session(session):
        await repair_report_if_incomplete(db, existing, session)
        await ensure_question_reviews(db, existing)
        return _report_to_read(existing)

    scores = [score.model_dump() for score in await list_scores(db, session_id)]
    report_stats = build_report_stats(session, scores)
    score_citations = [citation for score in scores for citation in score.get("citations") or []]

    if not is_final_report_session(session):
        report_data = fallback_report(scores, report_stats, session.target_position)
        citations = merge_citations(score_citations)
        now = datetime.utcnow()
        return InterviewReportRead(
            id=None,
            session_id=session_id,
            total_score=int(report_stats.get("total_score") or 0),
            summary=str(report_data.get("summary") or ""),
            strengths=report_data.get("strengths") or [],
            weaknesses=report_data.get("weaknesses") or [],
            suggestions=report_data.get("suggestions") or [],
            dimension_scores=report_stats.get("dimension_scores") or [],
            learning_path=report_data.get("learning_path") or [],
            sample_answer=str(report_data.get("sample_answer") or ""),
            citations=citations,
            is_final=False,
            generated_from_score_count=len(scores),
            created_at=now,
            updated_at=now,
        )

    report_data = await generate_report(
        [],
        scores,
        report_stats,
        session.target_position,
        session_id=session_id,
    )
    citations = merge_citations(score_citations, report_data.get("citations"))
    report = existing or InterviewReport(session_id=session_id)
    if existing is not None and not bool(existing.is_final):
        report.created_at = datetime.utcnow()
    report.total_score = int(report_stats.get("total_score") or 0)
    report.summary = str(report_data.get("summary") or "")
    report.strengths = json.dumps(report_data.get("strengths") or [], ensure_ascii=False)
    report.weaknesses = json.dumps(report_data.get("weaknesses") or [], ensure_ascii=False)
    report.suggestions = json.dumps(report_data.get("suggestions") or [], ensure_ascii=False)
    report.dimension_scores_json = json.dumps(
        report_stats.get("dimension_scores") or [], ensure_ascii=False
    )
    report.citations_json = citations_to_json(citations)
    report.learning_path = json.dumps(report_data.get("learning_path") or [], ensure_ascii=False)
    report.sample_answer = str(report_data.get("sample_answer") or "")
    report.is_final = True
    report.generated_from_score_count = len(scores)
    if existing is None:
        db.add(report)
    await db.flush()
    await ensure_question_reviews(db, report)
    return _report_to_read(report)


def is_final_report_session(session: InterviewSession) -> bool:
    """仅自然结束或主动结束的完整面试可以产生最终报告。"""
    return session.status == "finished" and session.session_purpose == "full_interview"


async def repair_report_if_incomplete(
    db: AsyncSession,
    report: InterviewReport,
    session: InterviewSession | None,
) -> bool:
    """根据已保存的评分修复缺少有效内容的报告，并保持已有引用不变。"""
    current = _report_content_dict(report)
    if not report_content_is_incomplete(current):
        return False

    scores = [score.model_dump() for score in await list_scores(db, report.session_id)]
    if not scores:
        return False
    report_stats = build_report_stats(session, scores)
    repaired = fallback_report(scores, report_stats, session.target_position if session else "")
    report.total_score = int(report_stats.get("total_score") or 0)
    report.summary = repaired["summary"]
    report.strengths = json.dumps(repaired["strengths"], ensure_ascii=False)
    report.weaknesses = json.dumps(repaired["weaknesses"], ensure_ascii=False)
    report.suggestions = json.dumps(repaired["suggestions"], ensure_ascii=False)
    report.dimension_scores_json = json.dumps(report_stats.get("dimension_scores") or [], ensure_ascii=False)
    report.learning_path = json.dumps(repaired["learning_path"], ensure_ascii=False)
    report.sample_answer = repaired["sample_answer"]
    await db.flush()
    return True


def build_report_stats(session: InterviewSession | None, scores: list[dict[str, Any]]) -> dict[str, Any]:
    """按面试计划维度聚合评分、短板和建议，并计算加权总分。"""
    plan = parse_plan(session.interview_plan_json if session else None)
    if not scores:
        return {
            "total_score": 0,
            "dimension_scores": [],
            "top_dimensions": [],
            "lowest_dimensions": [],
            "all_weaknesses": [],
            "all_suggestions": [],
        }

    buckets: dict[str, dict[str, Any]] = {}
    fallback_plan: list[dict[str, Any]] = []
    for score in scores:
        question_index = int(score.get("question_index") or 0)
        plan_item = get_plan_item_for_question(plan, question_index) if plan else {
            "dimension": str(score.get("dimension") or "综合表现"),
            "focus": "根据本题评分记录汇总。",
            "weight": 1,
        }
        dimension = str(plan_item.get("dimension") or "综合表现")
        if not plan:
            fallback_plan.append(plan_item)
        bucket = buckets.setdefault(
            dimension,
            {
                "dimension": dimension,
                "focus": str(plan_item.get("focus") or ""),
                "weight": float(plan_item.get("weight") or 0),
                "question_indexes": [],
                "scores": [],
                "weaknesses": [],
                "suggestions": [],
            },
        )
        bucket["question_indexes"].append(question_index)
        bucket["scores"].append(int(score.get("score") or 0))
        bucket["weaknesses"].extend(as_string_list(score.get("weaknesses")))
        bucket["suggestions"].extend(as_string_list(score.get("suggestions")))

    dimension_scores = []
    for bucket in buckets.values():
        avg_score = round(sum(bucket["scores"]) / len(bucket["scores"])) if bucket["scores"] else 0
        dimension_scores.append(
            {
                "dimension": bucket["dimension"],
                "score": avg_score,
                "question_indexes": sorted(set(bucket["question_indexes"])),
                "focus": bucket["focus"],
                "weaknesses": unique_keep_order(bucket["weaknesses"])[:5],
                "suggestions": unique_keep_order(bucket["suggestions"])[:5],
                "weight": bucket["weight"],
            }
        )

    total_score = weighted_total_score(dimension_scores)
    sorted_dimensions = sorted(dimension_scores, key=lambda item: item["score"], reverse=True)
    return {
        "total_score": total_score,
        "dimension_scores": dimension_scores,
        "top_dimensions": [item["dimension"] for item in sorted_dimensions[:2]],
        "lowest_dimensions": [item["dimension"] for item in sorted_dimensions[-2:]],
        "all_weaknesses": unique_keep_order([weakness for item in dimension_scores for weakness in item["weaknesses"]])[:8],
        "all_suggestions": unique_keep_order([suggestion for item in dimension_scores for suggestion in item["suggestions"]])[:8],
    }


def parse_plan(plan_json: str | None) -> list[dict[str, Any]]:
    """解析持久化的面试计划，仅接受 JSON 数组，非法内容返回空列表。"""
    if not plan_json:
        return []
    try:
        data = json.loads(plan_json)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def as_string_list(value: Any) -> list[str]:
    """将 JSON 字符串或列表规范化为非空字符串列表。"""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        value = parsed
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def unique_keep_order(values: list[str]) -> list[str]:
    """按首次出现顺序去除字符串列表中的重复项。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def weighted_total_score(dimension_scores: list[dict[str, Any]]) -> int:
    """按维度权重计算总分；无有效权重时回退到算术平均分。"""
    total_weight = sum(float(item.get("weight") or 0) for item in dimension_scores)
    if total_weight > 0:
        return round(sum(int(item["score"]) * float(item.get("weight") or 0) for item in dimension_scores) / total_weight)
    return round(sum(int(item["score"]) for item in dimension_scores) / len(dimension_scores)) if dimension_scores else 0


async def _list_messages(db: AsyncSession, session_id: int) -> list[dict]:
    """按消息 ID 升序查询会话消息，并转换为报告生成器所需的字典。"""
    result = await db.scalars(
        select(InterviewMessage)
        .where(InterviewMessage.session_id == session_id)
        .order_by(InterviewMessage.id.asc())
    )
    return [
        {
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
        for message in result
    ]


def _report_to_read(report: InterviewReport) -> InterviewReportRead:
    """兼容内部旧调用，将报告 ORM 对象转换为响应模型。"""
    return report_to_read(report)


def report_to_read(report: InterviewReport) -> InterviewReportRead:
    """将报告 ORM 对象转换为响应模型，并反序列化各 JSON 字段。"""
    return InterviewReportRead(
        id=report.id,
        session_id=report.session_id,
        total_score=report.total_score,
        summary=report.summary,
        strengths=json.loads(report.strengths or "[]"),
        weaknesses=json.loads(report.weaknesses or "[]"),
        suggestions=json.loads(report.suggestions or "[]"),
        dimension_scores=json.loads(report.dimension_scores_json or "[]"),
        learning_path=json.loads(report.learning_path or "[]"),
        sample_answer=report.sample_answer,
        citations=citations_from_json(report.citations_json),
        is_final=bool(report.is_final),
        generated_from_score_count=int(report.generated_from_score_count or 0),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


def _report_content_dict(report: InterviewReport) -> dict[str, Any]:
    """提取报告正文并解析列表栏目的 JSON 文本，供完整性检查和修复使用。"""
    return {
        "summary": report.summary,
        "strengths": json.loads(report.strengths or "[]"),
        "weaknesses": json.loads(report.weaknesses or "[]"),
        "suggestions": json.loads(report.suggestions or "[]"),
        "learning_path": json.loads(report.learning_path or "[]"),
        "sample_answer": report.sample_answer,
    }
