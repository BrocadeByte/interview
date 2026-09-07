QUESTIONS_BY_DIFFICULTY = {
    "easy": [
        "请先用 2 分钟介绍你的背景，并说明为什么想应聘这个岗位。",
        "你最熟悉的一个项目是什么？请说明你的职责和最终结果。",
        "你认为自己和目标岗位最匹配的三项能力是什么？",
        "遇到需求不明确时，你通常如何推进？",
        "请举例说明你最近一次学习新技术或新工具的过程。",
    ],
    "medium": [
        "结合你的项目经历，讲一个你独立解决复杂问题的案例。",
        "如果项目上线后出现线上问题，你会如何定位、沟通和复盘？",
        "请说明你对目标岗位核心能力的理解，并结合经历证明。",
        "讲一次你和他人意见不一致但最终推动事情落地的经历。",
        "如果让你重新做一个过往项目，你会优先优化哪里？为什么？",
    ],
    "hard": [
        "请拆解一个高复杂度项目的关键技术决策、取舍和风险控制。",
        "当业务目标和技术质量发生冲突时，你会如何判断优先级？",
        "请说明一次你主导系统性改进的经历，包括指标和结果。",
        "如果团队需要在两周内交付一个不确定性很高的需求，你会如何设计计划？",
        "请评价你当前能力和目标岗位要求之间的差距，并给出提升路径。",
    ],
}


def first_question(target_position: str, difficulty: str) -> str:
    """返回指定难度的首个问题，并在问题前补充目标岗位。"""
    question = QUESTIONS_BY_DIFFICULTY.get(difficulty, QUESTIONS_BY_DIFFICULTY["medium"])[0]
    return f"目标岗位是 {target_position}。{question}"


def next_question(target_position: str, difficulty: str, question_index: int, answer: str) -> str:
    """根据回答长度和题号返回追问、下一题或超出题库后的收尾问题。"""
    questions = QUESTIONS_BY_DIFFICULTY.get(difficulty, QUESTIONS_BY_DIFFICULTY["medium"])
    if len(answer) < 60:
        return "你的回答还比较概括。请补充一个具体场景：背景是什么、你做了什么、结果如何？"
    if question_index >= len(questions):
        return f"最后一个问题：如果你入职 {target_position}，前三个月你会如何证明自己的价值？"
    return questions[question_index]

