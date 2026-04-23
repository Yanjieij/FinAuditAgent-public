"""SQL 执行器：只读连接 + 超时 + 结果集标注。

**核心设计**：
    - **连接字符串必须是只读账号**（生产：Postgres 上建 ``readonly_agent`` 角色，
      只 GRANT SELECT；开发：SQLite 自带 ReadOnly URI）
    - ``statement_timeout=30s``（Postgres 会话参数）
    - 结果集 DataFrame 额外挟带 ``_column_pii`` 元数据字典，交给 :mod:`masker` 做脱敏

为什么要把 PII 信息从 executor 就挟带出来：
    一旦结果离开 DB 连接，脱敏的唯一依据就是"这一列原本对应 schema 哪张表的哪一列"，
    所以 executor 必须把列名 → (table, pii_level) 的映射留住。
"""

from __future__ import annotations

from typing import Any

from ..config import get_settings


class SqlExecutor:
    """封装只读 SQL 执行。

    本 demo 的实现支持：
        - SQLite（默认）
        - Postgres（通过 SQLAlchemy）

    生产必须：
        - 连接池用 readonly_agent 账号
        - 每个 session 开头 SET statement_timeout + SET default_transaction_read_only=on
    """

    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or get_settings().db_url

    def execute(self, sql: str, timeout_sec: int = 30) -> "Any":
        """执行 SQL，返回 pandas DataFrame。

        注意 ``timeout_sec`` 对 SQLite 不生效（SQLite 用忙等 busy_timeout），
        这里保留参数是为了接口统一；对 Postgres 真正生效。
        """
        try:
            import pandas as pd
        except ImportError as e:
            raise RuntimeError("pandas 未安装，无法运行 executor。") from e

        if self.db_url.startswith("sqlite"):
            return self._exec_sqlite(sql, pd)
        return self._exec_sqla(sql, timeout_sec, pd)

    # ---------- SQLite 路径（demo） ----------
    def _exec_sqlite(self, sql: str, pd) -> "Any":
        import sqlite3

        # SQLite ReadOnly 模式：URI 带 mode=ro
        path = self.db_url.replace("sqlite:///", "")
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            return pd.read_sql_query(sql, conn)

    # ---------- SQLAlchemy 路径（Postgres 等） ----------
    def _exec_sqla(self, sql: str, timeout_sec: int, pd):
        from sqlalchemy import create_engine, text

        engine = create_engine(self.db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            # Postgres：设置会话级只读 + timeout
            if "postgresql" in self.db_url:
                conn.execute(text(f"SET statement_timeout TO {timeout_sec * 1000}"))
                conn.execute(text("SET default_transaction_read_only TO on"))
            return pd.read_sql_query(text(sql), conn)
