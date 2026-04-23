"""命令行入口：``fin-audit ask "Q1 销售费用超支分析"``。

本 CLI 面向开发/面试 demo，生产上应由 FastAPI HTTP 端点替代。
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from .auth.token_context import UserToken, user_token_var

app = typer.Typer(add_completion=False, help="FinAuditAgent CLI")
console = Console()


@app.command()
def ask(
    question: str = typer.Argument(..., help="给 Agent 的自然语言问题"),
    user: str = typer.Option("u-dev", help="用户 ID（dev 场景）"),
    role: str = typer.Option("auditor", help="用户角色"),
    scopes: str = typer.Option(
        "read:finance,read:documents,compute:sandbox,notify:external",
        help="权限 scopes（逗号分隔）",
    ),
    thread_id: str = typer.Option("cli-default", help="会话 thread_id（用于 checkpoint resume）"),
):
    """发起一次问答。

    \b
    典型端到端流程：
      1. 设置 user_token（ContextVar）
      2. 构建并 invoke LangGraph
      3. 图会在 human_review 后停住（interrupt_before=["execute"]）
      4. CLI 打印审批载荷；模拟审批后 resume
    """
    _ensure_user_token(user, role, scopes.split(","))

    from .graph.builder import build_graph

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    console.print(Panel.fit(f"[bold cyan]问题[/bold cyan]: {question}"))
    state = graph.invoke({"question": question}, config=config)

    console.print(Panel.fit(
        f"[bold yellow]图已在 HumanReview 前停住[/bold yellow]\n"
        f"审批载荷 sig: {state.get('approval_token','')[:16]}...\n"
        f"所需角色: {state.get('approver_role_required','?')}",
        title="[yellow]HITL[/yellow]",
    ))

    if typer.confirm("审批人同意吗？", default=True):
        graph.update_state(config, {"approval_status": "approved"})
        final = graph.invoke(None, config=config)
        console.print(Panel.fit(
            f"[bold green]FINAL[/bold green]\n{final.get('final_answer','(无草稿)')}",
            title="报告",
        ))
    else:
        graph.update_state(config, {"approval_status": "rejected"})
        console.print("[red]已拒绝[/red]")


@app.command()
def show_audit():
    """校验审计日志链完整性。"""
    from .auth.audit_log import AuditLog

    ok, reason = AuditLog().verify_chain()
    console.print(f"Audit chain: [{'green' if ok else 'red'}]{reason}[/]")


def _ensure_user_token(sub: str, role: str, scopes: list[str]) -> None:
    tok = UserToken(sub=sub, role=role, scopes=tuple(s.strip() for s in scopes if s.strip()))
    user_token_var.set(tok)


if __name__ == "__main__":
    app()
