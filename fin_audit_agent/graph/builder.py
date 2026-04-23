"""组装 + 编译 LangGraph 主图。

调用示例::

    from fin_audit_agent.graph.builder import build_graph
    from fin_audit_agent.auth.token_context import user_token_var, UserToken

    graph = build_graph()

    # 上层 API 入口：set 用户 token 进 ContextVar
    user_token_var.set(UserToken(sub="u1", role="auditor", scopes=("read:finance",)))
    config = {"configurable": {"thread_id": "audit-2025-04-21-001"}}

    # 第一次 invoke：图会跑到 human_review 后停住（interrupt_before=["execute"]）
    result = graph.invoke({"question": "Q1 销售费用超支分析"}, config=config)
    print(result)  # 包含审批载荷 sig

    # 审批人同意后，update_state → resume
    graph.update_state(config, {"approval_status": "approved"})
    final = graph.invoke(None, config=config)
"""

from __future__ import annotations

from typing import Any

from .checkpoint import make_checkpointer
from .edges import route_after_approval, route_after_clarify, route_after_drafter
from .nodes import (
    node_analyze,
    node_clarify,
    node_data_fetch,
    node_doc_rag,
    node_drafter,
    node_execute,
    node_human_review,
    node_intake,
    node_notify,
    node_planner,
)
from .state import AgentState


def build_graph(
    *,
    checkpointer: Any | None = None,
    interrupt_before_execute: bool = True,
):
    """构造并编译主图。

    Args:
        checkpointer: 自定义 Saver；None 则按 config.db_url 自动选
        interrupt_before_execute: 是否在 Execute 前停图等审批。测试时可关
    """
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(AgentState)

    # 节点
    g.add_node("intake", node_intake)
    g.add_node("clarify", node_clarify)
    g.add_node("planner", node_planner)
    g.add_node("data_fetch", node_data_fetch)
    g.add_node("doc_rag", node_doc_rag)
    g.add_node("analyze", node_analyze)
    g.add_node("drafter", node_drafter)
    g.add_node("human_review", node_human_review)
    g.add_node("execute", node_execute)
    g.add_node("notify", node_notify)

    # 起点
    g.add_edge(START, "intake")
    g.add_edge("intake", "clarify")

    # Clarify 分岔
    g.add_conditional_edges(
        "clarify",
        route_after_clarify,
        {"planner": "planner", "END": END},
    )

    # Planner → 3 个并行子节点（demo 里简化为串行：实际生产可用 LangGraph 的 parallel 分支）
    g.add_edge("planner", "data_fetch")
    g.add_edge("data_fetch", "doc_rag")
    g.add_edge("doc_rag", "analyze")
    g.add_edge("analyze", "drafter")

    # Drafter 自 retry
    g.add_conditional_edges(
        "drafter",
        route_after_drafter,
        {"drafter": "drafter", "human_review": "human_review"},
    )

    # HumanReview → approval 分岔
    g.add_conditional_edges(
        "human_review",
        route_after_approval,
        {"execute": "execute", "END": END},
    )

    g.add_edge("execute", "notify")
    g.add_edge("notify", END)

    checkpointer = checkpointer or make_checkpointer()
    compile_kwargs: dict[str, Any] = {"checkpointer": checkpointer}
    if interrupt_before_execute:
        compile_kwargs["interrupt_before"] = ["execute"]

    return g.compile(**compile_kwargs)
