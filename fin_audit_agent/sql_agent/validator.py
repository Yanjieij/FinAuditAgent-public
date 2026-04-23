"""SQL 安全校验：AST 级 allowlist + LIMIT 注入。

**三重防线**（面试要讲清楚对应的威胁模型）：
    1. **语句类型 allowlist**：只允许 SELECT。任何 INSERT/UPDATE/DELETE/DROP/
       ALTER/CREATE/GRANT 全部拦截。即便 LLM 被 prompt-injection 诱导生成 DDL，
       也过不了 validator。
    2. **LIMIT 注入**：没显式 LIMIT 时自动加 `LIMIT 10000`，防止意外扫全表。
    3. **禁函数**：禁 ``pg_read_file`` / ``COPY`` / ``lo_export`` 等能触达 OS 的函数。

**为什么用 sqlglot 而不是正则**：
    正则永远绕得过。sqlglot 是**跨方言 AST 解析器**，能正确识别嵌套子查询、
    CTE、UNION 后面的 SELECT 等。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ValidateResult:
    ok: bool
    sql: str  # 通过（可能被改写加了 LIMIT）后的 SQL
    reason: str = ""


# ==== 高危函数黑名单（不完全，但覆盖常见触达 OS / 文件系统的函数） ====
_BLOCKED_FUNCS = {
    "pg_read_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "copy",
    "load_extension",
    "dblink",
    "dblink_exec",
}


def validate(sql: str, *, default_limit: int = 10_000, dialect: str = "sqlite") -> ValidateResult:
    """解析并校验 SQL，不过即返回 ok=False + 原因。

    Args:
        sql: 待校验的 SQL
        default_limit: 若没有 LIMIT 则注入这个值
        dialect: sqlglot 方言。本项目 demo 用 sqlite，生产改 "postgres"

    Returns:
        :class:`ValidateResult`，通过时 ``sql`` 字段是**改写后的最终 SQL**。
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as e:
        raise RuntimeError(
            "sqlglot 未安装。environment.yml 已列，请重建 conda 环境。"
        ) from e

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        return ValidateResult(False, sql, f"parse_error: {e}")

    # 1) allowlist：顶层必须是 SELECT（或 CTE 包一个 SELECT）
    top = tree
    if isinstance(top, exp.With):
        # CTE 包着的 SELECT
        top = top.this
    if not isinstance(top, exp.Select):
        return ValidateResult(False, sql, f"only SELECT allowed, got {type(top).__name__}")

    # 2) 禁函数
    for fn in tree.find_all(exp.Anonymous):
        name = (fn.name or "").lower()
        if name in _BLOCKED_FUNCS:
            return ValidateResult(False, sql, f"blocked_function: {name}")

    # sqlglot 也会用具体 Expression（例如 exp.Func 子类）表达常见函数；
    # 为了简单这里补一遍名字检查
    for node in tree.walk():
        fn_name = getattr(node, "sql_name", lambda: "")().lower() if hasattr(node, "sql_name") else ""
        if fn_name in _BLOCKED_FUNCS:
            return ValidateResult(False, sql, f"blocked_function: {fn_name}")

    # 3) 注入 LIMIT（只在最外层 Select；嵌套 subquery 不动）
    if not tree.args.get("limit"):
        tree.set("limit", exp.Limit(expression=exp.Literal.number(default_limit)))

    # 4) 渲染回文本
    safe_sql = tree.sql(dialect=dialect)
    return ValidateResult(True, safe_sql)


def dry_run_sql(sql: str) -> str:
    """给 ``retry_loop.explain_check`` 用的：把 SQL 包一层 ``EXPLAIN`` / ``LIMIT 0``。

    Postgres 用 ``EXPLAIN``；SQLite 用 ``EXPLAIN QUERY PLAN``；
    MySQL 的 ``EXPLAIN`` 也够用。这里统一返回一个最快的 dry-run 写法。
    """
    return f"SELECT * FROM ({sql}) AS _dryrun LIMIT 0"
