"""验证 LangGraph checkpointer resume：崩溃后状态恢复。"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _fake_planner(state): return {"plan": ["analyze: demo"]}
def _fake_drafter(state): return {"draft": "x 1 [[exec_id=abc123#cell=X]]",
                                    "verify_report": {"ok": True}, "iterations": 1}


@pytest.mark.skipif(True, reason="依赖真实 langgraph 包；本地跑通请去掉 skip")
def test_graph_resume_after_interrupt(tmp_path, dev_user_token):
    from fin_audit_agent.graph.builder import build_graph

    with patch("fin_audit_agent.graph.nodes.node_planner", _fake_planner), \
         patch("fin_audit_agent.graph.nodes.node_drafter", _fake_drafter):
        graph = build_graph(interrupt_before_execute=True)
        config = {"configurable": {"thread_id": "t-resume-1"}}
        state = graph.invoke({"question": "Q"}, config=config)
        assert state.get("approval_status") == "pending"

        graph.update_state(config, {"approval_status": "approved"})
        final = graph.invoke(None, config=config)
        assert "final_answer" in final
