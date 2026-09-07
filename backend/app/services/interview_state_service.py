import json

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import inspect

from app.agents.nodes.interview_planner import get_plan_item_for_question
from app.agents.state import InterviewState, create_initial_state
from app.models.interview import InterviewMemory, InterviewMessage, InterviewSession
from app.models.profile import UserProfile
from app.services.job_description_service import load_job_description_snapshot
from app.services.resume_service import load_resume_snapshot


def profile_to_dict(profile: UserProfile | None) -> dict:
    """把用户画像 ORM 对象转换成 LangGraph 状态里的普通字典。"""
    if not profile:
        return {}

    return {
        "age": profile.age,
        "education": profile.education,
        "major": profile.major,
        "experience_years": profile.experience_years,
        "target_position": profile.target_position,
        "target_city": profile.target_city,
        "expected_salary": profile.expected_salary,
        "skills": profile.skills,
        "projects": profile.projects,
        "self_evaluation": profile.self_evaluation,
    }


def messages_to_langchain(messages: list[InterviewMessage]) -> list[AIMessage | HumanMessage]:
    """把数据库消息转换成 LangChain 消息，并保留题号和追问信息。"""
    result: list[AIMessage | HumanMessage] = []
    for message in messages:
        metadata = {
            "question_index": message.question_index,
            "dimension": message.dimension,
            "is_followup": bool(message.is_followup),
            "followup_index": message.followup_index,
        }
        if message.role == "assistant":
            result.append(AIMessage(content=message.content, additional_kwargs=metadata))
        elif message.role == "user":
            result.append(HumanMessage(content=message.content, additional_kwargs=metadata))
    return result


def count_current_followups(messages: list[InterviewMessage], current_question_index: int) -> int:
    """统计当前主问题下已经问过多少次追问。"""
    return sum(
        1
        for message in messages
        if message.role == "assistant"
        and message.question_index == current_question_index
        and bool(message.is_followup)
    )


def build_state_from_session(
    session: InterviewSession,
    profile: UserProfile | None,
    messages: list[InterviewMessage] | None = None,
) -> InterviewState:
    """从数据库会话构造 LangGraph 状态，并过滤掉已压缩的旧消息。"""
    messages = messages if messages is not None else list(session.messages)
    session_memories = [] if "memories" in inspect(session).unloaded else list(session.memories)
    compressed_until_message_id = latest_compressed_until_message_id(session_memories)
    messages = [message for message in messages if message.id > compressed_until_message_id]
    state = create_initial_state(
        user_id=session.user_id,
        session_id=session.id,
        target_position=session.target_position,
        difficulty=session.difficulty,  # type: ignore[arg-type]  # 保留历史状态字段传参的类型检查兼容标记。
        profile=profile_to_dict(profile),
        mode=(getattr(session, "mode", None) or "training"),  # type: ignore[arg-type]  # 保留历史状态字段传参的类型检查兼容标记。
        interview_type=(getattr(session, "interview_type", None) or "mixed"),  # type: ignore[arg-type]  # 保留历史状态字段传参的类型检查兼容标记。
        resume=load_resume_snapshot(getattr(session, "resume_snapshot_json", None)),
        resume_id=getattr(session, "resume_id", None),
        target_job=load_job_description_snapshot(
            getattr(session, "job_description_snapshot_json", None),
            session.target_position,
        ),
        job_description_id=getattr(session, "job_description_id", None),
        parent_session_id=getattr(session, "parent_session_id", None),
        source_report_id=getattr(session, "source_report_id", None),
        source_weakness_key=getattr(session, "source_weakness_key", None),
        practice_context=load_practice_context(getattr(session, "practice_context_json", None)),
        session_purpose=(getattr(session, "session_purpose", None) or "full_interview"),  # type: ignore[arg-type]  # 保留历史状态字段传参的类型检查兼容标记。
        comparison_group_id=getattr(session, "comparison_group_id", None),
    )
    state["status"] = session.status  # type: ignore[assignment]  # 保留历史状态赋值的类型检查兼容标记。
    state["interview_plan"] = parse_interview_plan(session.interview_plan_json)
    state["current_question_index"] = session.current_question_index
    state["follow_up_count"] = count_current_followups(messages, session.current_question_index)
    state["messages"] = messages_to_langchain(messages)
    state["medium_term_memory"] = latest_session_memory(session_memories)

    for message in reversed(messages):
        if message.role == "assistant" and message.question_index == session.current_question_index:
            state["current_question"] = message.content
            if message.dimension:
                state["current_dimension"] = message.dimension
            break

    if not state["current_dimension"]:
        plan_item = get_plan_item_for_question(state["interview_plan"], session.current_question_index)
        state["current_dimension"] = plan_item["dimension"]

    return state


def latest_session_memory(memories: list[InterviewMemory]) -> str:
    """取最新 session_summary，作为后续提示词里的中期记忆。"""
    session_memories = [memory for memory in memories if memory.memory_type == "session_summary"]
    if not session_memories:
        return "暂无本场面试摘要。"
    session_memories.sort(key=lambda memory: (memory.updated_at, memory.id), reverse=True)
    return session_memories[0].summary or "暂无本场面试摘要。"


def latest_compressed_until_message_id(memories: list[InterviewMemory]) -> int:
    """读取最新中期记忆的压缩游标，用于避免旧消息重复进 prompt。"""
    session_memories = [memory for memory in memories if memory.memory_type == "session_summary"]
    if not session_memories:
        return 0
    session_memories.sort(key=lambda memory: (memory.updated_at, memory.id), reverse=True)
    try:
        metadata = json.loads(session_memories[0].metadata_json or "{}")
    except json.JSONDecodeError:
        return 0
    return int(metadata.get("compressed_until_message_id") or 0)


def parse_interview_plan(plan_json: str | None) -> list[dict]:
    """从会话字段恢复持久化的面试计划。"""
    if not plan_json:
        return []
    try:
        data = json.loads(plan_json)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def load_practice_context(context_json: str | None) -> dict:
    """读取不可变的练习来源快照，忽略格式无效的历史内容。"""
    if not context_json:
        return {}
    try:
        data = json.loads(context_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
