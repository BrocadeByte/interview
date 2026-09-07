import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question_review import QuestionReview
from app.models.report import InterviewReport
from app.models.score import InterviewScore
from app.schemas.question_review import QuestionReviewRead


async def ensure_question_reviews(
    db: AsyncSession,
    report: InterviewReport,
) -> list[QuestionReviewRead]:
    """为每条评分创建稳定的逐题复盘快照，并修复不完整的历史记录。"""
    await db.execute(
        select(InterviewReport.id)
        .where(InterviewReport.id == report.id)
        .with_for_update()
    )
    scores = list(await db.scalars(
        select(InterviewScore)
        .where(InterviewScore.session_id == report.session_id)
        .order_by(
            InterviewScore.question_index.asc(),
            InterviewScore.created_at.asc(),
            InterviewScore.id.asc(),
        )
    ))
    if not scores:
        return []

    existing = list(await db.scalars(
        select(QuestionReview)
        .where(QuestionReview.report_id == report.id)
        .order_by(
            QuestionReview.question_index.asc(),
            QuestionReview.created_at.asc(),
            QuestionReview.id.asc(),
        )
    ))
    reviews_by_score_id = {review.score_id: review for review in existing}
    changed = False

    for score in scores:
        review = reviews_by_score_id.get(score.id)
        if review is None:
            review = QuestionReview(
                report_id=report.id,
                session_id=report.session_id,
                score_id=score.id,
                **build_question_review_payload(score),
            )
            db.add(review)
            reviews_by_score_id[score.id] = review
            changed = True
        elif question_review_is_incomplete(review):
            apply_question_review_payload(review, build_question_review_payload(score))
            changed = True

    if changed:
        await db.flush()

    return [question_review_to_read(reviews_by_score_id[score.id]) for score in scores]


def build_question_review_payload(score: InterviewScore) -> dict[str, Any]:
    """根据已保存的评分构建完整且以事实为依据的逐题复盘快照。"""
    question_index = max(1, int(score.question_index or 0))
    dimension = _meaningful_text(score.dimension) or "综合表现"
    question = _meaningful_text(score.question) or f"第 {question_index} 题（原问题未完整保存）"
    original_answer = _meaningful_text(score.answer)
    answer = original_answer or "本题未记录有效回答；请先补充你的真实回答后再参考示范模板。"
    score_value = max(0, min(100, int(score.score or 0)))
    weaknesses = _string_list(score.weaknesses)
    suggestions = _string_list(score.suggestions)
    reason = _meaningful_text(score.reason)
    deduction_reasons = _unique_text(([reason] if reason else []) + weaknesses)
    if not deduction_reasons:
        deduction_reasons = [
            "本题评分记录未标注具体扣分项；请对照本题得分与建议结构补充复盘。"
        ]

    sub_scores = _int_dict(score.sub_scores)
    if not sub_scores:
        sub_scores = {"综合评分": score_value}
    suggested_structure = build_suggested_structure(dimension, suggestions)
    weakness_key = f"question_{question_index}"
    sample_answer = build_grounded_sample_answer(original_answer, suggestions)
    practice_seed = {
        "weakness_key": weakness_key,
        "target_dimension": dimension,
        "source_score_id": score.id,
        "question_index": question_index,
        "question": question,
        "answer": answer,
        "deduction_reasons": deduction_reasons,
        "suggestions": suggestions,
    }
    return {
        "question_index": question_index,
        "dimension": dimension,
        "question": question,
        "answer": answer,
        "score": score_value,
        "sub_scores_json": json.dumps(sub_scores, ensure_ascii=False),
        "deduction_reasons_json": json.dumps(deduction_reasons, ensure_ascii=False),
        "suggested_structure_json": json.dumps(suggested_structure, ensure_ascii=False),
        "sample_answer": sample_answer,
        "weaknesses_json": json.dumps(weaknesses, ensure_ascii=False),
        "weakness_key": weakness_key,
        "practice_seed_json": json.dumps(practice_seed, ensure_ascii=False),
    }


def build_suggested_structure(dimension: str, suggestions: list[str]) -> list[str]:
    """按项目行为、系统设计或基础知识维度选择回答结构，并补充首条改进建议。"""
    normalized = dimension.casefold()
    if any(keyword in normalized for keyword in ("项目", "协作", "沟通", "hr", "行为")):
        structure = [
            "背景：说明真实业务场景、目标和约束，不补写未经历的项目。",
            "任务：明确自己的真实职责、协作边界和需要解决的问题。",
            "行动：解释实际采取的步骤、方案取舍和关键细节。",
            "结果：补充真实结果或验证方式；没有量化指标时如实说明。",
            "复盘：总结学到的经验以及下一次会如何改进。",
        ]
    elif any(keyword in normalized for keyword in ("系统", "设计", "架构")):
        structure = [
            "需求：先澄清真实目标、规模、边界和关键约束。",
            "方案：给出整体设计，并说明核心组件之间的关系。",
            "取舍：解释一致性、性能、成本、复杂度等方面的真实权衡。",
            "验证：说明可观测性、异常处理和可基于真实场景验证的指标。",
        ]
    else:
        structure = [
            "结论：先直接回答问题，明确核心观点。",
            "原理：解释关键机制、流程和成立条件。",
            "边界：说明适用场景、限制、异常情况和常见误区。",
            "实践：只结合真实经历说明应用方式、取舍与验证结果。",
        ]

    if suggestions:
        structure.append(f"改进重点：结合真实信息落实评分建议——{suggestions[0]}")
    return structure


def build_grounded_sample_answer(original_answer: str, suggestions: list[str]) -> str:
    """返回不虚构候选人公司、指标或经历的示范回答模板。"""
    answer_excerpt = original_answer.strip()
    if len(answer_excerpt) > 600:
        answer_excerpt = f"{answer_excerpt[:600].rstrip()}……"
    improvement = suggestions[0] if suggestions else "结合本题评分补充最关键的缺失信息"
    if answer_excerpt:
        return (
            "示范模板（仅复用你已提供的信息，请把方括号内容替换为真实事实）："
            f"“我刚才回答的核心是：{answer_excerpt}。为了说明得更完整，我会先补充"
            "[真实背景、目标与约束]，再说明[本人实际职责和采取的关键行动]，解释"
            "[真实方案及取舍依据]，最后给出[真实结果、验证方式或如实说明暂无量化指标]，"
            f"并落实改进重点：[根据真实经历补充：{improvement}]。”"
        )
    return (
        "示范模板（当前信息不足，不代写经历；请把方括号内容替换为真实事实）："
        "“我的结论是[基于真实知识或经历填写结论]。当时的背景和约束是"
        "[真实背景与约束]，我负责[真实职责]，采取了[真实行动与方案]，选择该方案是因为"
        "[真实取舍依据]。最终结果是[真实结果或验证方式；没有量化指标时如实说明]，"
        f"复盘时我还会补充[根据真实情况落实：{improvement}]。”"
    )


def question_review_is_incomplete(review: QuestionReview) -> bool:
    """检查逐题复盘的核心文本、结构建议和练习来源是否完整有效。"""
    return not all((
        _meaningful_text(review.dimension),
        _meaningful_text(review.question),
        _meaningful_text(review.answer),
        _json_string_list(review.deduction_reasons_json),
        _json_string_list(review.suggested_structure_json),
        _meaningful_text(review.sample_answer),
        _json_dict(review.practice_seed_json),
        _meaningful_text(review.weakness_key),
    ))


def apply_question_review_payload(review: QuestionReview, payload: dict[str, Any]) -> None:
    """将构建好的复盘字段逐项写入数据库模型对象。"""
    for field, value in payload.items():
        setattr(review, field, value)


def question_review_to_read(review: QuestionReview) -> QuestionReviewRead:
    """解析复盘记录中的结构化 JSON 字段，并构造接口返回模型。"""
    return QuestionReviewRead(
        id=review.id,
        report_id=review.report_id,
        session_id=review.session_id,
        score_id=review.score_id,
        question_index=review.question_index,
        dimension=review.dimension,
        question=review.question,
        answer=review.answer,
        score=review.score,
        sub_scores=_json_int_dict(review.sub_scores_json),
        deduction_reasons=_json_string_list(review.deduction_reasons_json),
        suggested_structure=_json_string_list(review.suggested_structure_json),
        sample_answer=review.sample_answer,
        weaknesses=_json_string_list(review.weaknesses_json),
        weakness_key=review.weakness_key,
        practice_seed=_json_dict(review.practice_seed_json),
        created_at=review.created_at,
    )


def _meaningful_text(value: Any) -> str:
    """清理文本首尾空白，将常见空内容占位词转换为空字符串。"""
    text = str(value or "").strip()
    return text if text.casefold() not in {"无", "无内容", "暂无", "暂无内容", "none", "null"} else ""


def _string_list(value: Any) -> list[str]:
    """兼容普通文本与 JSON 列表输入，提取有效文本并按顺序去重。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            value = [value]
    if not isinstance(value, list):
        return []
    return _unique_text([_meaningful_text(item) for item in value])


def _int_dict(value: Any) -> dict[str, int]:
    """兼容字典和 JSON 输入，将有效分数限制到零至一百并跳过无法转换的条目。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        try:
            result[str(key)] = max(0, min(100, int(item)))
        except (TypeError, ValueError):
            continue
    return result


def _unique_text(values: list[str]) -> list[str]:
    """清理并过滤无效文本，按首次出现顺序去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _meaningful_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _json_value(value: str, fallback: Any) -> Any:
    """解析 JSON 文本，内容为空或解析失败时返回指定的默认值。"""
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json_string_list(value: str) -> list[str]:
    """将已存储的 JSON 文本解析为清理并去重后的字符串列表。"""
    return _string_list(_json_value(value, []))


def _json_int_dict(value: str) -> dict[str, int]:
    """将已存储的 JSON 文本解析为分数受限的整数字典。"""
    return _int_dict(_json_value(value, {}))


def _json_dict(value: str) -> dict[str, Any]:
    """将已存储的 JSON 文本解析为字典，解析失败或类型不符时返回空字典。"""
    parsed = _json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}
