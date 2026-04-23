"""Example 04 —— LangGraph FSM + HITL interrupt + resume。

演示：
    1. 构建主图（``interrupt_before=['execute']``）
    2. 第一次 invoke → 图跑到 human_review 后停
    3. 查 checkpoint state，看审批载荷和 HMAC sig
    4. 调 ``graph.update_state`` 模拟审批同意
    5. 第二次 ``invoke(None, config)`` → resume 到 END

**本 demo 不调真实 LLM**：为了让跑起来不烧 key，Planner/Drafter 都 mock 掉。
"""

from __future__ import annotations

from unittest.mock import patch

from rich.console import Console

from fin_audit_agent.auth.token_context import UserToken, user_token_var
from fin_audit_agent.graph.builder import build_graph

console = Console()


def _fake_planner(state):
    return {"plan": ["data_fetch: 查 Q1 销售费用", "analyze: 算同比"]}


def _fake_drafter(state):
    return {
        "draft": "Q1 销售费用 1,050,000 [[exec_id=deadbeef#cell=TOTAL]] 同比增长 8.2% [[exec_id=deadbeef#cell=YOY]]",
        "verify_report": {"ok": True, "violations": 0, "checked": 2},
        "iterations": 1,
    }


def main():
    user_token_var.set(UserToken(sub="u1", role="auditor",
                                  scopes=("read:finance", "compute:sandbox", "notify:external"),
                                  token_hash_prefix="abc12345"))

    with patch("fin_audit_agent.graph.nodes.node_planner", _fake_planner), \
         patch("fin_audit_agent.graph.nodes.node_drafter", _fake_drafter):

        graph = build_graph(interrupt_before_execute=True)
        config = {"configurable": {"thread_id": "demo-hitl-001"}}

        console.rule("[bold]第一次 invoke（会在 execute 前停）[/bold]")
        state = graph.invoke({"question": "Q1 销售费用是否异常？"}, config=config)

        console.print(f"draft: [cyan]{state.get('draft','')[:120]}[/cyan]")
        console.print(f"approval_status: {state.get('approval_status')}")
        console.print(f"approver_role_required: {state.get('approver_role_required')}")
        console.print(f"approval_token[:16]: {(state.get('approval_token') or '')[:16]}")

        snapshot = graph.get_state(config)
        console.print(f"\n[yellow]checkpoint 中下一个节点[/yellow]: {snapshot.next}")

        console.rule("[bold]模拟审批同意 → resume[/bold]")
        graph.update_state(config, {"approval_status": "approved"})
        final = graph.invoke(None, config=config)
        console.print(f"final_answer: [green]{final.get('final_answer','')[:120]}[/green]")
        console.print(f"saga_log: {final.get('saga_log', [])}")


if __name__ == "__main__":
    main()
