import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.score import InterviewScore
from app.schemas.score import InterviewScoreRead
from app.services.citation_service import citations_from_json, citations_to_json, normalize_citations


async def save_latest_score(db: AsyncSession, session_id: int, scores: list[dict[str, Any]]) -> InterviewScore | None:
    """保存面试图本次生成的最后一条评分；评分列表为空时不写入数据库。"""
    if not scores:
        return None

    score = scores[-1]
    question_index = int(score.get("question_index") or 0)
    citations = normalize_citations(score.get("citations"), question_index=question_index)
    record = InterviewScore(
        session_id=session_id,
        question_index=question_index,
        question=str(score.get("question") or ""),
        answer=str(score.get("answer") or ""),
        dimension=str(score.get("dimension") or ""),
        score=int(score.get("score") or 0),
        sub_scores=json.dumps(score.get("sub_scores") or {}, ensure_ascii=False),
        reason=str(score.get("reason") or ""),
        weaknesses=json.dumps(score.get("weaknesses") or [], ensure_ascii=False),
        suggestions=json.dumps(score.get("suggestions") or [], ensure_ascii=False),
        is_fallback=bool(score.get("is_fallback", False)),
        fallback_reason=str(score.get("fallback_reason")) if score.get("fallback_reason") else None,
        citations_json=citations_to_json(citations),
    )
    db.add(record)
    await db.flush()
    return record


async def list_scores(db: AsyncSession, session_id: int) -> list[InterviewScoreRead]:
    """按题号和创建时间升序查询指定面试会话的全部评分。"""
    result = await db.scalars(
        select(InterviewScore)
        .where(InterviewScore.session_id == session_id)
        .order_by(InterviewScore.question_index.asc(), InterviewScore.created_at.asc())
    )
    return [_score_to_read(score) for score in result]


def _score_to_read(score: InterviewScore) -> InterviewScoreRead:
    """将评分 ORM 对象转换为响应模型，并反序列化其中的 JSON 字段。"""
    return InterviewScoreRead(
        id=score.id,
        session_id=score.session_id,
        question_index=score.question_index,
        question=score.question,
        answer=score.answer,
        dimension=score.dimension,
        score=score.score,
        sub_scores=json.loads(score.sub_scores or "{}"),
        reason=score.reason,
        weaknesses=json.loads(score.weaknesses or "[]"),
        suggestions=json.loads(score.suggestions or "[]"),
        is_fallback=score.is_fallback,
        fallback_reason=score.fallback_reason,
        citations=citations_from_json(score.citations_json),
        created_at=score.created_at,
    )
