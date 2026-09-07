from langgraph.graph import END, START, StateGraph

from app.agents.nodes.answer_pipeline import answer_pipeline_node
from app.agents.nodes.interview_planner import get_interview_question_count, plan_interview_node
from app.agents.state import InterviewState


async def mark_finished_node(state: InterviewState) -> dict:
    """返回结束状态增量，统一生成面试完成提示。"""
    return {
        "status": "finished",
        "current_question": "本次模拟面试已完成。可以查看评分与复盘报告。",
    }


def route_by_action(state: InterviewState) -> str:
    """将接口层动作映射到统一面试图的入口节点。"""
    action = state.get("action", "start")
    if action == "start":
        return "plan_interview"
    if action == "answer":
        return "answer_pipeline"
    if action == "finish":
        return "mark_finished"
    raise ValueError(f"Unknown interview action: {action}")


def route_after_answer(state: InterviewState) -> str:
    """根据追问结果和主问题上限决定停在追问、结束面试或直接收尾。"""
    if state["followup_decision"] and state["followup_decision"]["needs_followup"]:
        return "end"

    if state["current_question_index"] >= get_interview_question_count(state["interview_plan"]):
        return "mark_finished"

    return "end"


def build_interview_graph():
    """编译面试状态图。

    start 路径负责规划并出首题；answer 路径在一次调用里完成评分、追问判断和下一题
    生成；finish 路径直接标记结束。节点只返回状态增量，持久化由接口服务在图执行
    完成后由 Service 统一处理。
    """
    graph = StateGraph(InterviewState)

    graph.add_node("plan_interview", plan_interview_node)
    graph.add_node("answer_pipeline", answer_pipeline_node)
    graph.add_node("mark_finished", mark_finished_node)

    graph.add_conditional_edges(
        START,
        route_by_action,
        {
            "plan_interview": "plan_interview",
            "answer_pipeline": "answer_pipeline",
            "mark_finished": "mark_finished",
        },
    )
    graph.add_edge("plan_interview", END)
    graph.add_conditional_edges(
        "answer_pipeline",
        route_after_answer,
        {
            "end": END,
            "mark_finished": "mark_finished",
        },
    )
    graph.add_edge("mark_finished", END)

    return graph.compile()


interview_graph = build_interview_graph()
