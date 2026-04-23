"""Example 07 —— 端到端：报销单审核场景主路径贯通。

**场景**：
    用户是审计员，上传一张报销单 PDF，问"这张报销单是否超预算？请给出审计意见。"

**期望的 trace**：
    Intake → Clarify → Planner
          → DocRAG（抽 KV：申请人=张三 金额=2720 类别=差旅）
          → DataFetch（SQL 查张三所在部门本月差旅预算）
          → Analyze（沙箱：计算超支率）
          → Drafter（输出含 [[exec_id:...]] 引证的审计意见 + citation bbox）
          → HITL 停住，模拟审批
          → Execute（Saga：记账 + 发飞书）
          → Notify → END

**本 demo 为避免烧 LLM key，把 Planner/Drafter 替换成确定性 mock**；
真实跑时把 patch 去掉并填 .env 的 OPENAI_API_KEY 即可。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from fin_audit_agent.auth.token_context import UserToken, user_token_var
from fin_audit_agent.graph.builder import build_graph
from fin_audit_agent.lineage.tracker import LineageTracker, Source, render_lineage_for_cli
from fin_audit_agent.rag.kv_extractor import extract as extract_kv
from fin_audit_agent.rag.layout import analyze_pdf
from fin_audit_agent.sandbox.runner import run_code

console = Console()

DEMO_DB = Path(".e2e_fin.db")


def seed():
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    conn = sqlite3.connect(DEMO_DB)
    conn.executescript(
        """
        CREATE TABLE dim_department (dept_id INT PRIMARY KEY, name TEXT, travel_budget DECIMAL);
        INSERT INTO dim_department VALUES (1,'市场部', 2000);
        """
    )
    conn.commit(); conn.close()


def _mock_data_fetch(state):
    """从"DB"查张三所在部门（市场部）的差旅预算。"""
    conn = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
    budget = conn.execute("SELECT travel_budget FROM dim_department WHERE name='市场部'").fetchone()[0]
    conn.close()
    return {
        "sql_results": [{
            "sql_id": "SQL-001",
            "sql": "SELECT travel_budget FROM dim_department WHERE name='市场部'",
            "rows": 1, "df_ref": "inline", "columns": ["travel_budget"],
            "masked_columns": [],
        }],
        # 顺手塞进 sandbox_execs 一个"查询结果 cell"，方便 Drafter 引证
    }


def _mock_doc_rag(state):
    blocks = analyze_pdf("demo.pdf")
    kv = extract_kv(blocks)
    return {
        "rag_chunks": [{
            "chunk_id": "REIMB-001:table",
            "doc_id": "REIMB-001",
            "page": 1, "bbox": [50, 150, 550, 320],
            "section_path": "差旅费报销单",
            "text": f"申请人={kv.applicant} 合计={kv.total_amount}",
            "score": 1.0,
        }],
    }


def _mock_analyze(state):
    # 调真实 sandbox！
    budget = 2000  # 从 sql_results 里能反推；这里写死是为了 demo 简单
    reimb = 2720   # 从 rag_chunks 里抽的金额

    code = f"""
BUDGET = {budget}
REIMBURSEMENT = {reimb}
OVERRUN_AMOUNT = REIMBURSEMENT - BUDGET
OVERRUN_RATE = OVERRUN_AMOUNT / BUDGET * 100     # 百分比
"""
    r = run_code(code)
    return {
        "sandbox_execs": [{
            "exec_id": r.exec_id,
            "ok": r.ok,
            "cells": r.cells,
            "stdout": r.stdout,
        }]
    }


def _mock_planner(state):
    return {"plan": ["doc_rag: 抽报销单 KV", "data_fetch: 查部门差旅预算", "analyze: 算超支率"]}


def _mock_drafter(state):
    execs = state.get("sandbox_execs") or []
    exec_id = execs[0]["exec_id"] if execs else "unknown"
    return {
        "draft": (
            f"# 审计意见\n"
            f"申请人张三的差旅费报销金额为 2720.00 元 "
            f"[citation:REIMB-001:table#page=1#bbox=50,150,550,320]，"
            f"而市场部本月差旅预算为 2000 元（SQL-001），"
            f"超支金额 720 元 [[exec_id={exec_id}#cell=OVERRUN_AMOUNT]]，"
            f"超支率 36% [[exec_id={exec_id}#cell=OVERRUN_RATE]]，"
            f"建议退回重审。"
        ),
        "verify_report": {"ok": True, "violations": 0, "checked": 2},
        "iterations": 1,
    }


def main():
    seed()
    user_token_var.set(UserToken(
        sub="audit-001", role="auditor",
        scopes=("read:finance", "read:documents", "compute:sandbox", "notify:external"),
        token_hash_prefix="e2eauditor1",
    ))

    with patch("fin_audit_agent.graph.nodes.node_planner", _mock_planner), \
         patch("fin_audit_agent.graph.nodes.node_drafter", _mock_drafter), \
         patch("fin_audit_agent.graph.nodes.node_data_fetch", _mock_data_fetch), \
         patch("fin_audit_agent.graph.nodes.node_doc_rag",   _mock_doc_rag), \
         patch("fin_audit_agent.graph.nodes.node_analyze",   _mock_analyze):

        graph = build_graph(interrupt_before_execute=True)
        config = {"configurable": {"thread_id": "e2e-001"}}

        console.rule("[bold cyan]第一次 invoke：跑到 HumanReview 停[/bold cyan]")
        state = graph.invoke({"question": "这张报销单是否超预算？"}, config=config)
        console.print(state.get("draft", ""))
        console.print(f"\n[yellow]approval_status[/yellow] = {state.get('approval_status')}")

        console.rule("[bold cyan]模拟审批同意 → resume[/bold cyan]")
        graph.update_state(config, {"approval_status": "approved"})
        final = graph.invoke(None, config=config)

        console.print(f"final_answer（摘要）: {final.get('final_answer','')[:200]}...")
        console.print(f"saga_log: {final.get('saga_log')}")

    # Lineage 演示（手动登记）
    console.rule("[bold cyan]数据血缘[/bold cyan]")
    tr = LineageTracker()
    exec_id = final["sandbox_execs"][0]["exec_id"] if final.get("sandbox_execs") else "x"
    tr.track("OVERRUN_AMOUNT", 720.0,
              [Source.exec_(exec_id, "OVERRUN_AMOUNT"),
               Source.doc("REIMB-001:table", 1, [50, 150, 550, 320]),
               Source.sql("SQL-001", col="travel_budget")],
              note="报销 − 预算")
    tr.track("OVERRUN_RATE", 36.0,
              [Source.exec_(exec_id, "OVERRUN_RATE")])
    console.print(render_lineage_for_cli(tr))


if __name__ == "__main__":
    main()
