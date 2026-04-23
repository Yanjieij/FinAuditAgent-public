"""Checkpoint Saver 工厂。

**LangGraph 内置了多种 Saver**：
    - ``MemorySaver``     —— 仅进程内存；demo 都不够用，重启就丢
    - ``SqliteSaver``     —— 文件 SQLite；单机 / 开发够用
    - ``PostgresSaver``   —— 生产级，配合 RLS / 多租户

**本项目策略**：
    - 默认回退到 ``SqliteSaver``（不需要装 Postgres 也能跑 examples）
    - 传入 Postgres URL 自动切到 ``PostgresSaver``（需 ``langgraph-checkpoint-postgres``）

为什么 checkpoint 重要（面试要讲）：
    - HITL 场景下 Agent 会"停很久"等审批，中间 worker 可能重启、挂掉、迁移
    - Saga execute 过程中单步失败要能 resume 到"上一次成功的步骤"
    - 有 checkpoint 才能谈幂等 + 补偿；没 checkpoint 这些都是纸上谈兵
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import get_settings


def make_checkpointer(db_path: str | Path | None = None, backend: str | None = None):
    """返回一个 LangGraph Checkpointer。

    Args:
        db_path: SQLite 文件路径（仅 sqlite backend 用）
        backend: "sqlite" | "postgres" | "memory"。None 则按 settings.db_url 自动选。

    Returns:
        一个可直接传给 ``StateGraph.compile(checkpointer=...)`` 的对象。
    """
    if backend is None:
        db_url = get_settings().db_url
        if db_url.startswith("postgres"):
            backend = "postgres"
        elif db_url.startswith("sqlite"):
            backend = "sqlite"
        else:
            backend = "memory"

    if backend == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    if backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = str(db_path or ".fin_audit_checkpoint.db")
        # LangGraph 0.2 的 SqliteSaver 需要一个 Connection
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        return SqliteSaver(conn)

    if backend == "postgres":
        # 生产路径，按需启用（environment.yml 注释了依赖）
        from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore

        db_url = get_settings().db_url
        saver = PostgresSaver.from_conn_string(db_url)
        saver.setup()  # 幂等：建表
        return saver

    raise ValueError(f"unknown backend: {backend}")
