"""验证 SQL validator 的 allowlist 和 LIMIT 注入。"""

from __future__ import annotations

import pytest

from fin_audit_agent.sql_agent.validator import validate


def test_select_allowed_and_limit_injected():
    r = validate("SELECT * FROM fact_expense")
    assert r.ok
    assert "LIMIT" in r.sql.upper()


def test_insert_blocked():
    r = validate("INSERT INTO fact_expense VALUES (1,1,1,'x','2025-01-01')")
    assert not r.ok
    assert "only SELECT" in r.reason


def test_delete_blocked():
    r = validate("DELETE FROM fact_expense WHERE id=1")
    assert not r.ok


def test_drop_blocked():
    r = validate("DROP TABLE fact_expense")
    assert not r.ok


def test_blocked_function():
    r = validate("SELECT pg_read_file('/etc/passwd')")
    # pg_read_file 是 Anonymous，本 validator 会命中
    assert not r.ok
    assert "blocked_function" in r.reason


def test_existing_limit_preserved():
    r = validate("SELECT * FROM fact_expense LIMIT 5")
    assert r.ok
    # 已有 LIMIT 不应被覆盖
    assert "LIMIT 5" in r.sql.upper().replace("LIMIT  ", "LIMIT ")


@pytest.mark.parametrize("sql", [
    "SELECT 1",                              # 常量 SELECT
    "SELECT amount FROM fact_expense",
    "WITH x AS (SELECT 1) SELECT * FROM x",  # CTE 也要通过
])
def test_various_selects_ok(sql):
    r = validate(sql)
    assert r.ok, r.reason
