"""条件路由 + 升级阶梯。

**LangGraph 条件边的两种写法**：
    1. ``add_conditional_edges`` 接受一个返回下一节点名的函数
    2. 函数通常读 state 里的"裁决字段"（verdict / approval_status）

本文件只放**路由函数**，真正 wire up 在 :mod:`builder`。
"""

from __future__ import annotations

from .state import AgentState


def route_after_clarify(state: AgentState) -> str:
    """Clarify 之后：若有 clarify_question，直接结束请用户回答；否则到 planner。"""
    if state.get("verdict") == "need_human" or state.get("clarify_question"):
        return "END"
    return "planner"


def route_after_drafter(state: AgentState) -> str:
    """Drafter 之后：
        - verify_report 不通过且未超迭代上限 → 回 drafter 重写
        - 否则 → human_review
    """
    report = state.get("verify_report") or {}
    iters = state.get("iterations", 0) or 0
    if not report.get("ok", True) and iters < 3:
        return "drafter"
    return "human_review"


def route_after_approval(state: AgentState) -> str:
    """HumanReview 之后（resume 回来时会走这里）：
        - approved  → execute
        - rejected / timeout → END（让上层 CLI 告诉用户）
        - pending  → END（图停住，等下次 resume）
    """
    status = state.get("approval_status", "")
    if status == "approved":
        return "execute"
    if status in {"rejected", "timeout"}:
        return "END"
    return "END"  # pending 也结束图；外部 update_state 后再 invoke(None)
