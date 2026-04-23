"""LangGraph 主图的各节点实现。

**设计原则**：
    - 每个节点返回**部分 state dict**，由 reducer 合并（messages 追加，其它覆盖）
    - 节点内尽量只做编排和 state 更新，真正的业务在 ``sql_agent/`` / ``sandbox/`` / ``rag/`` 等子包里
    - 节点函数签名都一样 ``(state: AgentState) -> dict``，方便 mock 测试

节点流程（顺序可在 builder.py 里改）::

    intake → clarify → planner
                           │
                           ▼
                  ┌────────┼────────┐
                  ▼        ▼        ▼
               data_fetch doc_rag analyze
                  │        │        │
                  └────────┼────────┘
                           ▼
                        drafter
                           │
                           ▼
                    human_review (interrupt)
                           │
                  ┌────────┴────────┐
             approved           rejected
                  │                  │
                  ▼                  ▼
                execute             end
                  │
                  ▼
                notify
                  │
                  ▼
                 END
"""

from __future__ import annotations

from typing import Any

from ..config import Limits, get_llm
from .hitl import build_approval_payload, required_role_for_amount
from .state import AgentState


# ---------------------------------------------------------------------------
# 1. Intake：受理用户请求
# ---------------------------------------------------------------------------
def node_intake(state: AgentState) -> dict:
    """记录问题，初始化 iterations。

    这个节点本身不调 LLM，就是 state 初始化——保持 Intake 确定性是金融系统的好习惯。
    """
    return {
        "question": state.get("question", ""),
        "iterations": 0,
        "verdict": "",
        "approval_status": "",
        "saga_log": [],
        "sql_results": [],
        "rag_chunks": [],
        "sandbox_execs": [],
    }


# ---------------------------------------------------------------------------
# 2. Clarify：若问题不清晰，反问
# ---------------------------------------------------------------------------
def node_clarify(state: AgentState) -> dict:
    """判断是否需要澄清，需要则生成澄清问题。

    这里可以调便宜模型做意图分类。demo 实现直接透传；真实场景用 schema_linker
    的"未命中率"来触发。
    """
    # 如果上游已经提出了 clarify_question（比如 retry_loop 降级传下来），直接冒泡
    if state.get("clarify_question"):
        return {"verdict": "need_human"}
    return {}


# ---------------------------------------------------------------------------
# 3. Planner：拆解任务
# ---------------------------------------------------------------------------
PLANNER_SYSTEM = """你是一个财务分析任务规划器。把用户问题拆成 2-5 个可执行步骤。
步骤必须是以下类型之一：
  - data_fetch: 需要查数据库（SQL）
  - doc_rag:    需要查文档（报销单、财报 PDF）
  - analyze:    需要在沙箱里做计算/可视化
返回 JSON: {"plan": ["data_fetch: ...", "analyze: ..."]}
"""


def node_planner(state: AgentState, llm: Any = None) -> dict:
    """规划步骤。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = llm or get_llm(kind="reasoning")
    msgs = [
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=state["question"]),
    ]
    resp = llm.invoke(msgs)
    import json
    import re

    raw = resp.content if hasattr(resp, "content") else str(resp)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        plan = data.get("plan", [])
    except json.JSONDecodeError:
        plan = ["analyze: 无法解析 plan，直接分析"]
    return {"plan": plan}


# ---------------------------------------------------------------------------
# 4. DataFetch / DocRAG / Analyze 子图入口节点
#    真实业务在对应子包里，这里是薄包装
# ---------------------------------------------------------------------------
def node_data_fetch(state: AgentState) -> dict:
    """调 sql_agent 流水线。真实实现调 ``sql_agent.retry_loop.run``。
    本节点骨架只收集 plan 里 data_fetch 类型的步骤，真实接入在 examples/07。"""
    data_steps = [p for p in state.get("plan", []) if p.startswith("data_fetch")]
    # 骨架：只返回结构化占位；examples/07_end_to_end_audit.py 会把真实调用接进来
    return {"sql_results": list(state.get("sql_results", []))}  # 保留已有


def node_doc_rag(state: AgentState) -> dict:
    """调 rag 流水线。"""
    return {"rag_chunks": list(state.get("rag_chunks", []))}


def node_analyze(state: AgentState) -> dict:
    """调 sandbox 做计算。"""
    return {"sandbox_execs": list(state.get("sandbox_execs", []))}


# ---------------------------------------------------------------------------
# 5. Drafter：生成含引证的报告
# ---------------------------------------------------------------------------
DRAFTER_SYSTEM = """你是一位严谨的财务分析师。基于 SQL 结果 / RAG 证据 / 沙箱计算结果，
写一份简洁的分析报告。铁律：

  - 报告里每一个数字后面必须紧跟 `[[exec_id=XXX#cell=YYY]]` 引证标签
  - 没有引证的数字一律不写
  - 结论要有依据，禁止编造

返回纯 Markdown 文本。
"""


def node_drafter(state: AgentState, llm: Any = None) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = llm or get_llm(kind="reasoning")
    context = _render_evidence(state)
    msgs = [
        SystemMessage(content=DRAFTER_SYSTEM),
        HumanMessage(content=f"# 用户问题\n{state['question']}\n\n# 证据材料\n{context}"),
    ]
    resp = llm.invoke(msgs)
    draft = resp.content if hasattr(resp, "content") else str(resp)

    # 跑一下 number_verifier 验证所有数字都带 evidence-pointer
    from ..sandbox.number_verifier import verify_numbers

    cells_by_exec = {
        e.get("exec_id"): e.get("cells", {}) for e in state.get("sandbox_execs", [])
    }
    report = verify_numbers(draft, cells_by_exec)
    return {
        "draft": draft,
        "verify_report": {"ok": report.ok, "violations": len(report.violations),
                          "checked": report.checked_count},
        "iterations": state.get("iterations", 0) + 1,
    }


def _render_evidence(state: AgentState) -> str:
    """把 state 里的 sql_results / rag_chunks / sandbox_execs 渲染成 prompt 证据块。"""
    lines: list[str] = []
    for r in state.get("sql_results", []) or []:
        lines.append(f"- SQL[{r.get('sql_id','?')}] rows={r.get('rows',0)} df_ref={r.get('df_ref','')}")
    for c in state.get("rag_chunks", []) or []:
        lines.append(f"- DOC[{c.get('chunk_id','?')}] p.{c.get('page')}: {c.get('text','')[:120]}")
    for e in state.get("sandbox_execs", []) or []:
        lines.append(f"- EXEC[{e.get('exec_id','?')}] cells={list(e.get('cells',{}).keys())}")
    return "\n".join(lines) or "(无证据；请先 data_fetch / doc_rag / analyze)"


# ---------------------------------------------------------------------------
# 6. HumanReview：计算审批载荷。真正的"停"由 interrupt_before 完成
# ---------------------------------------------------------------------------
def node_human_review(state: AgentState) -> dict:
    """计算审批载荷并挂到 state；graph 会在此节点之后 interrupt。"""
    amount = _guess_amount(state)
    role = required_role_for_amount(amount)
    _, sig = build_approval_payload(
        graph_id=state.get("question", "unknown")[:16],
        state=dict(state),
        required_role=role,
        amount=amount,
    )
    return {
        "approval_token": sig,
        "approval_status": "pending",
        "approver_role_required": role,
    }


def _guess_amount(state: AgentState) -> float:
    """从 sandbox_execs.cells 里尝试找"金额"类的 cell。

    真实系统会在 state 里显式约定一个 ``amount`` 字段由 Drafter/Planner 填。
    """
    for e in state.get("sandbox_execs", []) or []:
        for name, val in (e.get("cells") or {}).items():
            if any(k in name for k in ("AMOUNT", "TOTAL", "SUM")):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return 0.0


# ---------------------------------------------------------------------------
# 7. Execute：按 Saga 编排副作用
# ---------------------------------------------------------------------------
def node_execute(state: AgentState) -> dict:
    """骨架：真实的记账/通知/打款由 tools 实现。
    这里示例一个空的 Saga，实际在 examples/06_saga_rollback.py 展示完整用法。
    """
    from .saga import Saga

    saga = Saga(graph_id=state.get("question", "g")[:16])
    # demo: 空 saga，立即 ok
    result = saga.run(existing_log=state.get("saga_log", []))

    new_log: list[dict] = list(state.get("saga_log", []))
    for name in result.completed:
        new_log.append({"step": name, "status": "done", "idempotency_key": name})
    for name in result.compensated:
        new_log.append({"step": name, "status": "compensated", "idempotency_key": name})

    return {
        "saga_log": new_log,
        "verdict": "ok" if result.ok else "failed",
    }


# ---------------------------------------------------------------------------
# 8. Notify：发飞书/企微
# ---------------------------------------------------------------------------
def node_notify(state: AgentState) -> dict:
    """骨架：实际调 tools/notify_tool.py。"""
    # 这里只 compose 最终返回
    draft = state.get("draft", "")
    return {"final_answer": draft}
