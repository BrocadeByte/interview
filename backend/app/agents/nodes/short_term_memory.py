from langchain_core.messages import AnyMessage


def format_short_term_memory(
    messages: list[AnyMessage],
    current_question_index: int,
    recent_limit: int = 8,
    *,
    exclude_latest_exchange: bool = False,
) -> str:
    """按原始顺序汇总最近消息和当前主问题的全部问答，标注题号、追问及角色。

    最近消息数量受 recent_limit 限制，当前主问题的消息始终保留；无消息时返回提示文本。
    """
    if not messages:
        return "暂无历史问答。"

    excluded_indexes = _latest_exchange_indexes(messages) if exclude_latest_exchange else set()
    selected_indexes = set(range(max(0, len(messages) - recent_limit), len(messages))) - excluded_indexes
    for index, message in enumerate(messages):
        if index not in excluded_indexes and _metadata(message).get("question_index") == current_question_index:
            selected_indexes.add(index)

    lines: list[str] = []
    for index in sorted(selected_indexes):
        message = messages[index]
        meta = _metadata(message)
        question_index = int(meta.get("question_index") or 0)
        is_followup = bool(meta.get("is_followup") or False)
        followup_index = int(meta.get("followup_index") or 0)
        role = "AI" if message.type == "ai" else "用户"

        if message.type == "ai":
            if is_followup:
                label = f"第 {question_index} 题追问 {followup_index}"
            else:
                label = f"第 {question_index} 题主问题"
        else:
            if is_followup:
                label = f"第 {question_index} 题追问 {followup_index} 的回答"
            else:
                label = f"第 {question_index} 题回答"

        lines.append(f"{label}｜{role}：{message.content}")

    return "\n".join(lines) if lines else "暂无历史问答。"


def _latest_exchange_indexes(messages: list[AnyMessage]) -> set[int]:
    """定位最新的“问题 + 回答”；元数据异常时只排除最新回答。"""
    answer_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].type == "human"),
        None,
    )
    if answer_index is None:
        return set()

    excluded = {answer_index}
    answer_metadata = _exchange_metadata(messages[answer_index])
    for index in range(answer_index - 1, -1, -1):
        message = messages[index]
        if message.type == "human":
            # 连续用户消息表明顺序异常，不猜测更早的 AI 消息与其配对。
            break
        if message.type == "ai":
            if answer_metadata and _exchange_metadata(message) == answer_metadata:
                excluded.add(index)
            break
    return excluded


def _exchange_metadata(message: AnyMessage) -> tuple[int, bool, int] | None:
    """返回用于配对问答的题号与追问元数据，缺失题号时不强行配对。"""
    metadata = _metadata(message)
    if metadata.get("question_index") in (None, ""):
        return None
    try:
        return (
            int(metadata["question_index"]),
            bool(metadata.get("is_followup") or False),
            int(metadata.get("followup_index") or 0),
        )
    except (TypeError, ValueError):
        return None


def _metadata(message: AnyMessage) -> dict:
    """复制消息附加的题号、追问等元数据，未提供元数据时返回空字典。"""
    return dict(getattr(message, "additional_kwargs", {}) or {})
