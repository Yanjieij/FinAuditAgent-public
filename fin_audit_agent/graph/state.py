"""AgentState —— LangGraph 主图的状态类型。

**设计要点**：
    - 用 ``TypedDict`` + ``Annotated[list, add_messages]`` 的 LangGraph 惯用组合
    - 除了 ``messages`` 之外，其它字段都是**纯数据 + 显式覆盖**（不需要 reducer）
    - 关键字段：
        - ``plan``            —— Planner 输出的步骤清单
        - ``sql_results``     —— SQL 查询结果（每次查询一个 entry，含 sql_id / df_ref）
        - ``rag_chunks``      —— RAG 召回的段落（带引证）
        - ``sandbox_execs``   —— 每次沙箱执行的 ExecResult（含 cells）
        - ``draft``           —— Drafter 产生的报告草稿
        - ``approval_token``  —— HITL 审批载荷的 HMAC 签名
        - ``saga_log``        —— Execute 节点各步的幂等/补偿记录
        - ``iterations``      —— 防死循环
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

try:
    from langgraph.graph.message import add_messages
except ImportError:  # pragma: no cover
    # 降级实现：add_messages 的本质就是追加消息
    def add_messages(left: list, right: list) -> list:  # type: ignore
        return (left or []) + (right or [])


class SQLQueryRecord(TypedDict, total=False):
    """一次 SQL 查询的全量记录。"""

    sql_id: str            # 本次查询的全局 ID
    sql: str               # 最终执行的 SQL
    rows: int              # 返回行数
    df_ref: str            # 结果 DataFrame 的引用（artifact 路径）
    columns: list[str]
    masked_columns: list[str]   # 被脱敏的列


class RAGChunkRef(TypedDict, total=False):
    """一段 RAG 命中的 chunk（带引证）。"""

    chunk_id: str
    doc_id: str
    page: int
    bbox: list[float]     # [x0, y0, x1, y1]
    section_path: str     # "合并资产负债表 > 流动资产 > 应收账款"
    text: str
    score: float


class SagaStep(TypedDict, total=False):
    """Saga 补偿事务里的一步。"""

    step: str                        # "record_ledger" / "notify_feishu" / ...
    idempotency_key: str
    status: Literal["pending", "done", "failed", "compensated"]
    payload: dict[str, Any]
    error: str


class AgentState(TypedDict, total=False):
    """顶层 FSM 状态（透过 checkpoint 持久化）。"""

    # ---- 基础 ----
    messages: Annotated[list, add_messages]
    question: str
    clarify_question: str | None
    iterations: int

    # ---- Planner 产出 ----
    plan: list[str]

    # ---- 各子图的结果 ----
    sql_results: list[SQLQueryRecord]
    rag_chunks: list[RAGChunkRef]
    sandbox_execs: list[dict[str, Any]]   # ExecResult.asdict() 列表

    # ---- Drafter ----
    draft: str
    verify_report: dict[str, Any]         # sandbox.number_verifier.VerifyReport

    # ---- HITL ----
    approval_token: str | None            # 审批载荷的 HMAC
    approval_status: Literal["pending", "approved", "rejected", "timeout", ""]
    approver_role_required: str           # "preparer" / "approver" / "cfo"

    # ---- Saga ----
    saga_log: list[SagaStep]

    # ---- 终态 ----
    final_answer: str
    verdict: Literal["ok", "retry", "need_human", "failed", ""]
