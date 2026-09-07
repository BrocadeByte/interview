from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas.llm_outputs import InterviewReportOutput, is_meaningful_report_text
from app.services.knowledge_service import format_knowledge_context, unpack_knowledge_context
from app.services.llm_json import (
    as_list,
    as_str,
    clamp_int,
    parse_json_model_with_repair,
    parse_json_object,
)
from app.services.llm_service import llm
from app.services.prompt_security import format_untrusted_data, secure_system_prompt


SYSTEM_PROMPT = """
你是一个专业的 AI 面试复盘报告生成器。你要根据候选人的面试问答、每题评分和后端结构化统计，生成一份结构化面试报告。
要求：
1. total_score 必须使用后端结构化统计里的 total_score，不要自行编造。
2. summary 是整体表现总结。
3. strengths 是候选人核心优势列表，不能为空。
4. weaknesses 是主要短板列表，不能为空。
5. suggestions 是可执行优化建议列表，不能为空。
6. learning_path 是后续学习路线列表，不能为空。
7. sample_answer 给出一个针对本场最关键问题的完整优化示范回答，不能使用“无内容”等占位文本。
8. 优先结合知识库里的岗位能力模型、评分标准和优秀回答样例。
9. 各报告栏目必须参考后端结构化统计里的维度分、短板和建议。
10. 必须输出 JSON，不要输出 Markdown。
JSON 格式：
{
  "total_score": 75,
  "summary": "整体总结",
  "strengths": ["优势1", "优势2"],
  "weaknesses": ["短板1", "短板2"],
  "suggestions": ["建议1", "建议2"],
  "learning_path": ["学习路线1", "学习路线2"],
  "sample_answer": "优化后的示范回答"
}
""".strip()


async def generate_report(
    messages: list[dict],
    scores: list[dict],
    report_stats: dict,
    target_position: str,
) -> dict:
    """检索岗位知识并调用模型生成复盘报告，补齐无效栏目后附上知识引用。"""
    current_dimension = str(scores[-1].get("dimension") or "") if scores else ""
    knowledge_result = await format_knowledge_context(
        query=f"{target_position} {current_dimension} 面试报告 评分标准",
        limit=5,
        target_position=target_position,
        purpose="report",
        with_citations=True,
    )
    knowledge_text, citations = unpack_knowledge_context(knowledge_result)

    user_prompt = f"""
面试问答记录：
{format_untrusted_data("interview_records", messages)}

每题评分记录：
{format_untrusted_data("score_records", scores)}

后端结构化统计：
{report_stats}

请结合知识库中的岗位能力模型、评分标准和优秀回答样例，生成更贴近目标岗位的完整面试复盘报告。
知识库参考：
{format_untrusted_data("retrieved_knowledge_context", knowledge_text)}
""".strip()

    response = await llm.ainvoke([
        SystemMessage(content=secure_system_prompt(SYSTEM_PROMPT)),
        HumanMessage(content=user_prompt),
    ])
    try:
        output = await parse_json_model_with_repair(
            response.content,
            llm=llm,
            output_model=InterviewReportOutput,
            max_retries=1,
        )
        data = output.model_dump()
    except Exception:
        # 先保留首次响应中可用的字段，再依据可信的评分统计补齐缺失内容。
        try:
            data = parse_json_object(response.content)
        except Exception:
            data = {}

    report = ensure_report_completeness(
        normalize_report(data),
        scores=scores,
        report_stats=report_stats,
        target_position=target_position,
    )
    report["citations"] = citations
    return report


def fallback_report(
    scores: list[dict],
    report_stats: dict | None = None,
    target_position: str = "",
) -> dict:
    """根据已有评分和统计生成兜底报告，不依赖模型调用。

    总分优先采用后端统计值，无法转换时使用逐题平均分；结合维度表现、短板和建议填充栏目。
    """
    stats = report_stats or {}
    dimension_scores = [item for item in as_list(stats.get("dimension_scores")) if isinstance(item, dict)]
    total_score = clamp_int(stats.get("total_score"), default=_average_score(scores))
    position = target_position.strip() or "目标岗位"
    sorted_dimensions = sorted(
        dimension_scores,
        key=lambda item: clamp_int(item.get("score")),
        reverse=True,
    )
    top_dimensions = sorted_dimensions[:2]
    lowest_dimensions = list(reversed(sorted_dimensions[-2:]))
    top_names = "、".join(str(item.get("dimension") or "综合表现") for item in top_dimensions)
    low_names = "、".join(str(item.get("dimension") or "综合表现") for item in lowest_dimensions)

    if total_score >= 85:
        performance = "整体表现优秀，知识掌握和岗位匹配度较高"
    elif total_score >= 70:
        performance = "整体表现良好，已具备岗位所需的主要基础"
    elif total_score >= 60:
        performance = "整体基本达标，但关键知识的准确性和完整性仍需加强"
    else:
        performance = "整体表现仍有明显提升空间，需要优先补齐核心能力短板"

    summary = f"本次{position}面试总分为 {total_score} 分，{performance}。"
    if len(sorted_dimensions) == 1:
        summary += f"本次主要评估了{top_names}，建议结合下方短板继续做专项复盘。"
    elif sorted_dimensions:
        summary += f"相对稳定的维度是{top_names}，后续应优先提升{low_names}。"

    strengths = [
        f"{item.get('dimension') or '综合表现'}得分 {clamp_int(item.get('score'))} 分，是本场相对表现较稳定的维度。"
        for item in top_dimensions
    ] or ["已完成本次面试问答，并形成了可用于后续复盘的完整评分记录。"]

    weaknesses = _meaningful_items(stats.get("all_weaknesses"), limit=5)
    if not weaknesses:
        weaknesses = [
            f"{item.get('dimension') or '综合表现'}当前得分 {clamp_int(item.get('score'))} 分，相关知识深度和回答完整性仍需提升。"
            for item in lowest_dimensions
        ]
    if not weaknesses:
        weaknesses = ["当前回答对关键机制、适用边界和项目结果的说明还不够完整。"]

    suggestions = _meaningful_items(stats.get("all_suggestions"), limit=5)
    if not suggestions:
        suggestions = [
            f"围绕{item.get('dimension') or '核心能力'}补充原理、适用边界、异常处理和项目量化结果。"
            for item in lowest_dimensions
        ]
    if not suggestions:
        suggestions = ["使用“结论、原理、项目场景、方案取舍、量化结果”的结构重新组织关键问题答案。"]

    learning_path = [f"第{index}阶段：{item}" for index, item in enumerate(suggestions[:3], start=1)]
    return {
        "total_score": total_score,
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "suggestions": suggestions,
        "learning_path": learning_path,
        "sample_answer": _build_sample_answer(scores, suggestions),
    }


def ensure_report_completeness(
    report: dict,
    *,
    scores: list[dict],
    report_stats: dict,
    target_position: str,
) -> dict:
    """保留报告中有效的正文，用兜底内容补齐空缺或占位文本，并校准总分。"""
    fallback = fallback_report(scores, report_stats, target_position)
    completed = dict(report)
    # 总分以后端评分数据为准，不采用模型生成的分值。
    completed["total_score"] = fallback["total_score"]

    for field in ("summary", "sample_answer"):
        if not is_meaningful_report_text(as_str(completed.get(field))):
            completed[field] = fallback[field]

    for field in ("strengths", "weaknesses", "suggestions", "learning_path"):
        items = _meaningful_items(completed.get(field))
        completed[field] = items or fallback[field]
    return completed


def report_content_is_incomplete(data: dict[str, Any]) -> bool:
    """检查六个正文栏目，有效栏目不足三个时判定报告内容不完整。"""
    meaningful_sections = 0
    for field in ("summary", "sample_answer"):
        meaningful_sections += int(is_meaningful_report_text(as_str(data.get(field))))
    for field in ("strengths", "weaknesses", "suggestions", "learning_path"):
        meaningful_sections += int(bool(_meaningful_items(data.get(field))))
    return meaningful_sections < 3


def normalize_report(data: dict) -> dict:
    """统一报告的文本与列表字段类型，并将总分转换为零到一百之间的整数。"""
    return {
        "total_score": clamp_int(data.get("total_score")),
        "summary": as_str(data.get("summary")),
        "strengths": [as_str(item) for item in as_list(data.get("strengths"))],
        "weaknesses": [as_str(item) for item in as_list(data.get("weaknesses"))],
        "suggestions": [as_str(item) for item in as_list(data.get("suggestions"))],
        "learning_path": [as_str(item) for item in as_list(data.get("learning_path"))],
        "sample_answer": as_str(data.get("sample_answer")),
    }


def _average_score(scores: list[dict]) -> int:
    """将逐题分数转换并限制到零至一百分后，计算取整的平均分；无记录时返回零。"""
    if not scores:
        return 0
    return round(sum(clamp_int(score.get("score")) for score in scores) / len(scores))


def _meaningful_items(value: Any, *, limit: int | None = None) -> list[str]:
    """将输入转为文本列表，去除空白和占位条目，并按需截取指定数量。"""
    items = [as_str(item).strip() for item in as_list(value)]
    result = [item for item in items if is_meaningful_report_text(item)]
    return result[:limit] if limit is not None else result


def _build_sample_answer(scores: list[dict], suggestions: list[str]) -> str:
    """围绕最低分题目和前三条建议生成兜底回答指引，无题目时使用通用描述。"""
    weakest_score = min(scores, key=lambda item: clamp_int(item.get("score")), default={})
    question = as_str(weakest_score.get("question")).strip()
    topic = f"针对问题“{question}”" if question else "针对本场最需要改进的问题"
    key_points = "；".join(suggestions[:3])
    return (
        f"{topic}，可以先给出明确结论，再解释核心机制和适用边界，"
        "随后结合真实项目说明方案选择、异常处理、性能影响与量化结果，最后总结方案取舍。"
        f"本次回答应重点补充：{key_points}"
    )
