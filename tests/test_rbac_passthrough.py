"""验证 ContextVar 透传 + scope 守卫 + 审计日志链。"""

from __future__ import annotations

import pytest

from fin_audit_agent.auth.audit_log import AuditLog
from fin_audit_agent.auth.token_context import (
    UserToken,
    current_user,
    require_scope,
    user_token_var,
)


def test_current_user_fails_without_token():
    token = user_token_var.set(None)
    try:
        with pytest.raises(PermissionError):
            current_user()
    finally:
        user_token_var.reset(token)


def test_require_scope(dev_user_token):
    require_scope("read:finance")  # OK
    with pytest.raises(PermissionError):
        require_scope("approve:expense")  # 没这个 scope


def test_audit_log_chain(tmp_path, dev_user_token):
    db = tmp_path / "audit.db"
    log = AuditLog(db_path=db, secret="test-secret")
    log.append(dev_user_token, "sql.query", input_hash="h1", output_hash="o1")
    log.append(dev_user_token, "sandbox.run", input_hash="h2", output_hash="o2")
    log.append(dev_user_token, "notify.feishu", input_hash="h3", output_hash="o3")

    ok, reason = log.verify_chain()
    assert ok, reason


def test_audit_log_chain_tampering_detected(tmp_path, dev_user_token):
    db = tmp_path / "audit_tamper.db"
    log = AuditLog(db_path=db, secret="test-secret")
    log.append(dev_user_token, "sql.query")
    log.append(dev_user_token, "sandbox.run")

    # 直接改一行内容（模拟运维篡改）
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_log SET action='admin.delete' WHERE id=1")
    conn.commit()
    conn.close()

    ok, reason = log.verify_chain()
    assert not ok
    assert "sig_mismatch" in reason or "chain_break" in reason
