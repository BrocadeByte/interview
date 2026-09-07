import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview import InterviewMemory, InterviewMessage, InterviewSession
from app.models.score import InterviewScore
from app.schemas.llm_outputs import InterviewMemoryOutput
from app.services.llm_json import parse_json_model_with_repair
from app.services.llm_service import llm
from app.services.prompt_security import format_untrusted_data, secure_system_prompt


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
你是一个面试过程记忆整理器。你要把一段较早的面试问答压缩进本场面试的中期记忆。
要求：
1. 保留已考察主题、候选人表现、优势、短板、后续关注点。
2. 合并已有摘要和新增问答，不要重复堆砌原始问答。
3. 摘要要服务于后续提问、追问和评分，优先保留有判断价值的信息。
4. 必须输出 JSON，不要输出 Markdown。
JSON 格式：{
  "summary": "更新后的本场面试摘要",
  "covered_topics": ["已覆盖主题"],
  "strengths": ["优势"],
  "weaknesses": ["短板"],
  "next_focus": ["后续关注点"]
}
""".strip()


QUESTION_SUMMARY_PROMPT = """
你是一个面试过程记忆整理器。你要把本场面试中已经完成的一个主问题链路总结进中期记忆。
要求：
1. 保留已考察主题、候选人表现、优势、短板、后续关注点。
2. 不要重复堆砌原始问答，要压缩成可供后续提问和评分使用的摘要。
3. 必须输出 JSON，不要输出 Markdown。
JSON 格式：{
  "summary": "更新后的本场面试摘要",
  "covered_topics": ["已覆盖主题"],
  "strengths": ["优势"],
  "weaknesses": ["短板"],
  "next_focus": ["后续关注点"]
}
""".strip()


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

    user_prompt = f"""
目标岗位：{session.target_position}
面试难度：{session.difficulty}

已有本场面试中期记忆：
{format_untrusted_data("existing_interview_memory", previous_summary)}

本次需要压缩进中期记忆的较早问答：
{format_untrusted_data("interview_history", compact_text)}

请输出更新后的本场面试中期记忆。
""".strip()

    try:
        response = await llm.ainvoke([
            SystemMessage(content=secure_system_prompt(SYSTEM_PROMPT)),
            HumanMessage(content=user_prompt),
        ])
        output = await parse_json_model_with_repair(
            response.content,
            llm=llm,
            output_model=InterviewMemoryOutput,
            max_retries=1,
        )
        data = output.model_dump()
    except Exception as exc:
        logger.warning(
            "memory.medium.compact_failed session_id=%s compact_until_message_id=%s error=%r",
            session.id,
            compact_until_id,
            exc,
        )
        return

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

    previous_summary = await get_session_memory_summary(db, session.id)
    thread_text = format_question_thread(question_messages)
    score_text = format_score(latest_score)

    user_prompt = f"""
目标岗位：{session.target_position}
面试难度：{session.difficulty}
已存在的本场面试摘要：
{format_untrusted_data("existing_interview_memory", previous_summary)}

刚完成的第 {question_index} 题问答链路：
{format_untrusted_data("interview_history", thread_text)}

本题最新评分：
{score_text}

请输出更新后的本场面试中期记忆。
""".strip()

    response = None
    try:
        response = await llm.ainvoke([
            SystemMessage(content=secure_system_prompt(QUESTION_SUMMARY_PROMPT)),
            HumanMessage(content=user_prompt),
        ])
        output = await parse_json_model_with_repair(
            response.content,
            llm=llm,
            output_model=InterviewMemoryOutput,
            max_retries=1,
        )
        data = output.model_dump()
    except Exception as exc:
        logger.warning(
            "memory.medium.parse_failed session_id=%s question_index=%s error=%s raw=%r",
            session.id,
            question_index,
            exc,
            str(response.content)[:1000] if response is not None else "",
        )
        data = {
            "summary": previous_summary,
            "covered_topics": [],
            "strengths": [],
            "weaknesses": [],
            "next_focus": ["本题中期记忆解析失败，后续可根据原始问答和评分继续追问。"],
        }
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
