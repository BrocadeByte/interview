import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview import InterviewMemory, InterviewMessage, InterviewSession
from app.models.score import InterviewScore


logger = logging.getLogger(__name__)


MEMORY_TRIGGER_CHARS = 6000
KEEP_RECENT_MESSAGES = 8
MIN_COMPACT_CHARS = 1500


async def get_session_memory_summary(db: AsyncSession, session_id: int) -> str:
    """读取整场面试当前最新的中期记忆摘要。"""
    memory = await db.scalar(
        select(InterviewMemory)
        .where(InterviewMemory.session_id == session_id, InterviewMemory.memory_type == "session_summary")
        .order_by(InterviewMemory.updated_at.desc(), InterviewMemory.id.desc())
    )
    if not memory:
        return "暂无本场面试摘要。"
    return memory.summary or "暂无本场面试摘要。"


async def maybe_compact_medium_term_memory(
    db: AsyncSession,
    session: InterviewSession,
    *,
    trigger_chars: int = MEMORY_TRIGGER_CHARS,
    keep_recent_messages: int = KEEP_RECENT_MESSAGES,
    min_compact_chars: int = MIN_COMPACT_CHARS,
) -> None:
    """当未压缩上下文超过阈值时，把较早问答压缩进 session_summary。"""
    messages = sorted(list(session.messages), key=lambda message: message.id)
    if len(messages) <= keep_recent_messages:
        logger.info("memory.medium.skip reason=not_enough_messages session_id=%s message_count=%s", session.id, len(messages))
        return

    session_summary = await get_session_summary_memory(db, session.id)
    previous_summary = session_summary.summary if session_summary and session_summary.summary else "暂无本场面试摘要。"
    compressed_until_id = get_compressed_until_message_id(session_summary)

    uncompressed_messages = [message for message in messages if message.id > compressed_until_id]
    total_context_chars = len(previous_summary) + len(format_question_thread(uncompressed_messages))
    if total_context_chars < trigger_chars:
        logger.info(
            "memory.medium.skip reason=below_threshold session_id=%s chars=%s threshold=%s",
            session.id,
            total_context_chars,
            trigger_chars,
        )
        return

    keep_ids = {message.id for message in uncompressed_messages[-keep_recent_messages:]}
    keep_ids.update(message.id for message in uncompressed_messages if message.question_index == session.current_question_index)
    compact_messages = [message for message in uncompressed_messages if message.id not in keep_ids]
    compact_text = format_question_thread(compact_messages)

    if len(compact_text) < min_compact_chars:
        logger.info(
            "memory.medium.skip reason=compact_text_too_short session_id=%s chars=%s min_chars=%s",
            session.id,
            len(compact_text),
            min_compact_chars,
        )
        return

    compact_until_id = max(message.id for message in compact_messages)
    question_indexes = sorted({message.question_index for message in compact_messages})

    scores = list(
        await db.scalars(
            select(InterviewScore)
            .where(
                InterviewScore.session_id == session.id,
                InterviewScore.question_index.in_(question_indexes),
            )
            .order_by(InterviewScore.question_index.asc(), InterviewScore.created_at.asc())
        )
    )
    scored_indexes = {score.question_index for score in scores}
    if not set(question_indexes).issubset(scored_indexes):
        logger.info(
            "memory.medium.skip reason=missing_structured_scores session_id=%s question_indexes=%s",
            session.id,
            question_indexes,
        )
        return

    data = build_memory_from_scores(scores, previous_summary=previous_summary)
    summary = str(data.get("summary") or previous_summary)
    metadata = {
        **data,
        "compressed_until_message_id": compact_until_id,
        "compacted_message_count": len(compact_messages),
        "compacted_question_indexes": question_indexes,
        "trigger_chars": trigger_chars,
        "kept_recent_messages": keep_recent_messages,
    }

    if session_summary:
        session_summary.question_index = max(question_indexes) if question_indexes else session.current_question_index
        session_summary.summary = summary
        session_summary.metadata_json = json.dumps(metadata, ensure_ascii=False)
    else:
        db.add(
            InterviewMemory(
                session_id=session.id,
                question_index=max(question_indexes) if question_indexes else session.current_question_index,
                memory_type="session_summary",
                summary=summary,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )
        )

    await db.flush()
    logger.info(
        "memory.medium.compacted session_id=%s compact_until_message_id=%s compacted_messages=%s summary_preview=%r",
        session.id,
        compact_until_id,
        len(compact_messages),
        summary[:200],
    )


async def get_session_summary_memory(db: AsyncSession, session_id: int) -> InterviewMemory | None:
    """读取负责提示词使用的 session_summary 记忆记录。"""
    return await db.scalar(
        select(InterviewMemory)
        .where(InterviewMemory.session_id == session_id, InterviewMemory.memory_type == "session_summary")
        .order_by(InterviewMemory.updated_at.desc(), InterviewMemory.id.desc())
    )


def get_compressed_until_message_id(memory: InterviewMemory | None) -> int:
    """从 metadata_json 中取出已经压缩到哪条消息。"""
    if not memory:
        return 0
    try:
        metadata = json.loads(memory.metadata_json or "{}")
    except json.JSONDecodeError:
        return 0
    return int(metadata.get("compressed_until_message_id") or 0)


async def update_medium_term_memory(
    db: AsyncSession,
    session: InterviewSession,
    question_index: int,
    latest_score: InterviewScore | None,
) -> None:
    """旧方案：每完成一个主问题链路就摘要一次；当前主流程不再调用。"""
    question_messages = [
        message
        for message in session.messages
        if message.question_index == question_index
    ]
    if not question_messages:
        logger.info("memory.medium.skip reason=no_question_messages session_id=%s question_index=%s", session.id, question_index)
        return
    if latest_score is None:
        logger.info("memory.medium.skip reason=no_structured_score session_id=%s question_index=%s", session.id, question_index)
        return

    previous_summary = await get_session_memory_summary(db, session.id)
    data = build_memory_from_scores([latest_score], previous_summary=previous_summary)
    summary = str(data.get("summary") or previous_summary)

    db.add(
        InterviewMemory(
            session_id=session.id,
            question_index=question_index,
            memory_type="question_summary",
            summary=summary,
            metadata_json=json.dumps(data, ensure_ascii=False),
        )
    )

    session_summary = await db.scalar(
        select(InterviewMemory).where(
            InterviewMemory.session_id == session.id,
            InterviewMemory.memory_type == "session_summary",
        )
    )
    if session_summary:
        session_summary.question_index = question_index
        session_summary.summary = summary
        session_summary.metadata_json = json.dumps(data, ensure_ascii=False)
    else:
        db.add(
            InterviewMemory(
                session_id=session.id,
                question_index=question_index,
                memory_type="session_summary",
                summary=summary,
                metadata_json=json.dumps(data, ensure_ascii=False),
            )
        )
    await db.flush()
    logger.info("memory.medium.updated session_id=%s question_index=%s summary_preview=%r", session.id, question_index, summary[:200])


def build_memory_from_scores(
    scores: list[InterviewScore],
    *,
    previous_summary: str,
) -> dict[str, object]:
    """依据已保存评分生成确定性中期记忆，不重复发送原始问答给模型。"""
    covered_topics = _unique_non_empty([score.dimension for score in scores])
    strengths = _unique_non_empty(
        [
            f"{score.dimension}（{score.score} 分）：{score.reason[:300]}"
            for score in scores
            if score.score >= 75 and score.reason
        ]
    )
    weaknesses = _unique_non_empty(
        [item for score in scores for item in _json_string_list(score.weaknesses)]
    )
    suggestions = _unique_non_empty(
        [item for score in scores for item in _json_string_list(score.suggestions)]
    )
    score_summaries = [
        f"第 {score.question_index} 题（{score.dimension}）{score.score} 分：{score.reason[:300]}"
        for score in scores
    ]
    parts = []
    if previous_summary and previous_summary != "暂无本场面试摘要。":
        parts.append(previous_summary)
    parts.extend(score_summaries)
    return {
        "summary": "\n".join(_unique_non_empty(parts)) or "暂无本场面试摘要。",
        "covered_topics": covered_topics,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "next_focus": suggestions,
        "source": "structured_scores",
    }


def _json_string_list(value: str | None) -> list[str]:
    """把评分 JSON 列表安全恢复为文本列表，损坏值不进入记忆。"""
    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _unique_non_empty(values: list[str]) -> list[str]:
    """按原顺序去除空文本和重复项。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def format_question_thread(messages: list[InterviewMessage]) -> str:
    """把数据库消息格式化成给 LLM 阅读的问答文本。"""
    lines: list[str] = []
    for message in messages:
        if message.role == "assistant":
            if message.is_followup:
                label = f"追问 {message.followup_index}"
            else:
                label = "主问题"
            speaker = "AI"
        else:
            if message.is_followup:
                label = f"追问 {message.followup_index} 的回答"
            else:
                label = "主问题回答"
            speaker = "用户"
        lines.append(f"{label}｜{speaker}：{message.content}")
    return "\n".join(lines)


def format_score(score: InterviewScore | None) -> str:
    """把本题评分格式化成可放进记忆摘要提示词的 JSON 文本。"""
    if not score:
        return "暂无评分。"
    return json.dumps(
        {
            "score": score.score,
            "dimension": score.dimension,
            "reason": score.reason,
            "weaknesses": json.loads(score.weaknesses or "[]"),
            "suggestions": json.loads(score.suggestions or "[]"),
        },
        ensure_ascii=False,
    )
