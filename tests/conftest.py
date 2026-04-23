"""pytest 公用 fixtures：

    - ``fake_llm``：一个可控输出的假 LLM（对 LangChain Messages 接口）
    - ``tmp_db``：每个测试独立的 SQLite 临时库
    - ``dev_user_token``：设置一个 dev 用户 token，供需要 token 的工具测试用
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest


class FakeChatModel:
    """Mock LangChain Chat 模型。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    def invoke(self, messages):
        content = self._responses.pop(0) if self._responses else ""
        return type("M", (), {"content": content})()


@pytest.fixture
def fake_llm_factory():
    def _make(responses: list[str]) -> FakeChatModel:
        return FakeChatModel(responses)
    return _make


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """生成一个带玩具数据的 SQLite DB，返回路径。"""
    p = tmp_path / "fin.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE fact_expense (id INT, dept_id INT, amount DECIMAL, category TEXT, occurred_at DATE);
        INSERT INTO fact_expense VALUES
          (1,1,180000,'营销','2025-01-10'),
          (2,1,220000,'营销','2025-02-15'),
          (3,1,650000,'营销','2025-03-25');
        """
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def dev_user_token():
    """设置一个带全量 scope 的 dev token，测试结束自动回收。"""
    from fin_audit_agent.auth.token_context import UserToken, user_token_var

    tok = UserToken(
        sub="test-u1",
        role="auditor",
        scopes=("read:finance", "read:documents", "compute:sandbox", "notify:external"),
        token_hash_prefix="testhash0000",
    )
    token = user_token_var.set(tok)
    yield tok
    user_token_var.reset(token)
