import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.nodes.interview_planner import (
    DEFAULT_INTERVIEW_PLAN,
    get_interview_question_count,
    get_plan_item_for_question,
)
from app.agents.nodes.short_term_memory import format_short_term_memory
from app.agents.state import InterviewState
from app.schemas.llm_outputs import AnswerPipelineOutput
from app.services.citation_service import normalize_citations
from app.services.knowledge_service import format_knowledge_context, unpack_knowledge_context
from app.services.llm_json import parse_json_model_with_repair
from app.services.llm_service import llm
from app.services.llm_stream import invoke_json_with_streaming_field
from app.services.prompt_security import format_untrusted_data, secure_system_prompt


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
你是一个严格、专业的 AI 面试官，需要在一次输出中完成三件事：对本轮回答评分、判断是否追问、生成下一道要展示的问题。
输出要求：
1. 必须输出 JSON，不要输出 Markdown。
2. 字段顺序固定：先输出 needs_followup、question，再输出 decision_reason、score、sub_scores、reason、weaknesses、suggestions。question 字段会被流式推送给候选人，必须尽量靠前。
3. needs_followup 为 true 时，question 写围绕当前维度和候选人回答缺口的追问；为 false 且不是最后一题时，question 写下一道主问题（必须围绕下一计划考察维度）；为 false 且是最后一题时，question 输出空字符串。
4. score 使用 0 到 100 的整数；sub_scores 至少包含专业准确性、表达清晰度、项目真实性、岗位匹配度。
5. reason 说明评分理由；weaknesses 写本题暴露的问题；suggestions 写可执行改进建议。
6. 一次只问一个问题，不要重复已经问过的主问题或追问。
7. 训练模式可以用诊断性追问定位回答缺口；实战模式的 question 必须保持真实面试的中性表达，不得透露得分、标准答案、短板或改进建议，但仍需在后台完整输出评分字段供最终报告使用。
JSON 格式：{
  "needs_followup": false,
  "question": "下一道要展示的问题",
  "decision_reason": "是否追问的判断理由",
  "score": 80,
  "sub_scores": {"专业准确性": 80, "表达清晰度": 75, "项目真实性": 85, "岗位匹配度": 78},
  "reason": "评分理由",
  "weaknesses": ["不足1", "不足2"],
  "suggestions": ["建议1", "建议2"]
}
""".strip()


EXPECTED_SCHEMA = """
{
  "needs_followup": false,
  "question": "下一道要展示的问题",
  "decision_reason": "是否追问的判断理由",
  "score": 80,
  "sub_scores": {"专业准确性": 80, "表达清晰度": 75, "项目真实性": 85, "岗位匹配度": 78},
  "reason": "评分理由",
  "weaknesses": ["不足1", "不足2"],
  "suggestions": ["建议1", "建议2"]
}
""".strip()


def _get_last_user_answer(state: InterviewState) -> str:
    """从消息历史中倒序查找最近一次用户回答，没有用户消息时返回空字符串。"""
    for message in reversed(state["messages"]):
        if message.type == "human":
            return str(message.content)
    return ""


def _normalize_question(value: str) -> str:
    """移除问题文本中的所有空白并统一大小写，供重复问题检测使用。"""
    return "".join(str(value or "").split()).casefold()


def _asked_questions(state: InterviewState) -> set[str]:
    """汇总历史面试官消息与当前问题的规范化文本，用于判断新问题是否重复。"""
    return {
        normalized
        for message in state["messages"]
        if message.type == "ai"
        for normalized in [_normalize_question(str(message.content))]
        if normalized
    } | {_normalize_question(state["current_question"])}


def build_fallback_question(target_position: str, dimension: str, focus: str) -> str:
    """结合目标岗位、考察维度和重点生成兜底的下一题，避免重复泛化的开场问题。"""
    position = target_position.strip() or "目标岗位"
    dimension_name = dimension.strip() or "岗位能力"
    focus_text = focus.strip().rstrip("。") or f"围绕{dimension_name}说明你的实际做法"
    return (
        f"接下来考察「{dimension_name}」。请结合你应聘{position}的实际经历，"
        f"说明一个与“{focus_text}”相关的具体案例，以及你采取的关键措施和结果。"
    )


async def answer_pipeline_node(state: InterviewState) -> dict:
    """一次知识检索 + 一次模型调用，完成评分、追问判断和下一题生成。

    下一题维度按下一计划项归类；模型异常时使用兜底分与兜底问题，保证状态图
    可继续运行。
    """
    answer = _get_last_user_answer(state)
    plan = state["interview_plan"]
    current_index = state["current_question_index"]
    total_question_count = get_interview_question_count(plan)
    current_plan_item = get_plan_item_for_question(plan, current_index)
    current_dimension = str(state.get("current_dimension") or "").strip() or current_plan_item["dimension"]

    # 下一道主问题所属的计划项：不追问时按它归类，避免沿用已答题目维度。
    next_index = min(current_index + 1, total_question_count)
    next_plan_item = get_plan_item_for_question(plan, next_index) if plan else DEFAULT_INTERVIEW_PLAN[0]
    next_dimension = next_plan_item["dimension"]
    next_focus = next_plan_item["focus"]

    is_last_question = current_index >= total_question_count
    reached_max_followup = state["follow_up_count"] >= state["max_follow_up_count"]

    history_text = format_short_term_memory(
        state["messages"],
        current_question_index=current_index,
        recent_limit=8,
    )
    # 一次检索同时服务评分标准和下一题素材。
    knowledge_query = (
        f"{state['target_position']} {current_dimension} {state['current_question']} {answer[:200]} "
        f"{next_dimension} {next_focus}"
    )
    knowledge_result = await format_knowledge_context(
        query=knowledge_query,
        limit=4,
        target_position=state["target_position"],
        purpose="answer",
        with_citations=True,
    )
    knowledge_text, raw_citations = unpack_knowledge_context(knowledge_result)
    citations = [
        citation.model_dump(mode="json")
        for citation in normalize_citations(raw_citations, question_index=current_index)
    ]

    followup_instruction = (
        "这是最后一题且已达最大追问次数，必须 needs_followup=false 且 question 输出空字符串。"
        if is_last_question and reached_max_followup
        else f"已达最大追问次数，必须 needs_followup=false；question 写围绕下一维度「{next_dimension}」的主问题。"
        if reached_max_followup
        else f"如果需要追问，question 写围绕当前维度「{current_dimension}」和回答缺口的追问；"
        f"如果不追问，question 写围绕下一维度「{next_dimension}」（重点：{next_focus}）的下一道主问题。"
    )
    if is_last_question:
        followup_instruction += "若不追问，这是最后一题，question 输出空字符串。"

    mode = state.get("mode") or "training"
    mode_guidance = (
        "训练模式：可以用诊断性追问定位回答缺口，下一题可结合本轮表现渐进调整；不要直接把标准答案写进问题。"
        if mode == "training"
        else "实战模式：追问和下一题保持中性、真实，不得在 question 中透露得分、答案、短板或改进建议；评分只在后台保存。"
    )

    user_prompt = f"""
目标岗位：{state["target_position"]}
面试难度：{state["difficulty"]}
面试模式：{mode}
模式反馈策略：{mode_guidance}
面试类型：{state.get("interview_type", "mixed")}
会话用途：{state.get("session_purpose", "full_interview")}
来源短板 key：{format_untrusted_data("source_weakness_key", state.get("source_weakness_key"))}
当前题号：{current_index}（本场主问题数 {total_question_count}）
当前考察维度：{current_dimension}
当前计划考察重点：{current_plan_item["focus"]}
下一计划考察维度：{next_dimension}
下一维度考察重点：{next_focus}
当前追问次数：{state["follow_up_count"]}
最大追问次数：{state["max_follow_up_count"]}

候选人画像：{format_untrusted_data("candidate_profile", state["profile"])}

当前面试问题：{state["current_question"]}

候选人本轮回答：{format_untrusted_data("candidate_answer", answer)}

本场面试中期记忆：
{format_untrusted_data("interview_memory", state["medium_term_memory"])}

短期记忆（结构化历史问答）：
{format_untrusted_data("interview_history", history_text)}

知识库参考：
{format_untrusted_data("retrieved_knowledge_context", knowledge_text)}

追问与下一题规则：{followup_instruction}

请先判断是否追问并写出要展示的问题，再对本轮回答评分。
""".strip()

    decision_reason = "模型输出异常，默认不追问。"
    try:
        response = await invoke_json_with_streaming_field(
            llm,
            [SystemMessage(content=secure_system_prompt(SYSTEM_PROMPT)), HumanMessage(content=user_prompt)],
            field="question",
            # 模型输出先完整校验并由 Service 提交；API 随后只流式发送权威问题。
            stream_field=False,
        )
        output = await parse_json_model_with_repair(
            response.content,
            llm=llm,
            output_model=AnswerPipelineOutput,
            max_retries=1,
        )
        decision_reason = output.decision_reason
        weaknesses = output.weaknesses
        needs_followup = output.needs_followup and not reached_max_followup
        question_text = output.question.strip()
        score_result = {
            "question_index": current_index,
            "question": state["current_question"],
            "answer": answer,
            "dimension": current_dimension,
            "score": output.score,
            "sub_scores": output.sub_scores,
            "reason": output.reason,
            "weaknesses": weaknesses,
            "suggestions": output.suggestions,
            "is_fallback": False,
            "fallback_reason": None,
            "citations": citations,
        }
    except Exception as exc:
        logger.exception(
            "answer.pipeline.failed session_id=%s question_index=%s error=%r",
            state["session_id"],
            current_index,
            exc,
        )
        weaknesses = ["评分模型输出格式异常，无法可靠提取本轮短板。"]
        needs_followup = False
        question_text = ""
        score_result = {
            "question_index": current_index,
            "question": state["current_question"],
            "answer": answer,
            "dimension": current_dimension,
            "score": 60,
            "sub_scores": {},
            "reason": "评分模型输出格式异常，本轮使用系统兜底评分，建议人工复核。",
            "weaknesses": weaknesses,
            "suggestions": ["请稍后重试，或结合原始问答进行人工复核。"],
            "is_fallback": True,
            "fallback_reason": f"{type(exc).__name__}: {exc}",
            "citations": citations,
        }

    update: dict = {
        "scores": [score_result],
        "weaknesses": weaknesses,
        "followup_decision": {
            "needs_followup": needs_followup,
            "reason": decision_reason,
            "followup_question": "",
        },
    }

    asked_questions = _asked_questions(state)
    question_is_duplicate = _normalize_question(question_text) in asked_questions

    if needs_followup:
        # 空文本或重复追问不进入业务状态，直接进入下一主问题。
        if not question_text or question_is_duplicate:
            needs_followup = False
            update["followup_decision"]["needs_followup"] = False
            update["followup_decision"]["reason"] = (
                "追问与历史问题重复，默认进入下一题。"
                if question_is_duplicate
                else "追问未给出问题文本，默认进入下一题。"
            )
        else:
            new_followup_count = state["follow_up_count"] + 1
            update["followup_decision"]["followup_question"] = question_text
            update["follow_up_count"] = new_followup_count
            update["current_question"] = question_text
            update["current_dimension"] = current_dimension
            update["messages"] = [
                AIMessage(
                    content=question_text,
                    additional_kwargs={
                        "question_index": current_index,
                        "is_followup": True,
                        "followup_index": new_followup_count,
                    },
                )
            ]
            return update

    # 不追问：最后一题交由 mark_finished 节点输出结束语，否则生成下一道主问题。
    if is_last_question:
        return update

    fallback_question = build_fallback_question(
        state["target_position"],
        next_dimension,
        next_focus,
    )
    if reached_max_followup or not question_text or question_is_duplicate:
        question_text = fallback_question
    update["current_question"] = question_text
    update["current_dimension"] = next_dimension
    update["messages"] = [
        AIMessage(
            content=question_text,
            additional_kwargs={
                "question_index": next_index,
                "is_followup": False,
                "followup_index": 0,
            },
        )
    ]
    return update
