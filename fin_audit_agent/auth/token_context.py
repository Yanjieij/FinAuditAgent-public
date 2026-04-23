"""用户态 Token 透传（``ContextVar``）。

**为什么用 ContextVar 而不是全局变量 / threading.local**：

    LangGraph 的节点可能在 asyncio task 里跑、可能在 thread pool 里跑。
    ``ContextVar`` 是 PEP 567 定义的 **"任务上下文感知"** 变量：
        - 同一个 asyncio task 内赋值，后续的 await 点都能读到
        - 不同 task 之间自动隔离（即使共享线程）
        - 对 thread pool：每个 executor task 拿到的是**调度时的** context 快照

    这几乎是传递"请求级数据"（用户身份、trace_id、tenant_id）的唯一正确姿势。

用法::

    from fin_audit_agent.auth.token_context import user_token_var, UserToken

    # 上层 API 入口：
    token = user_token_var.set(UserToken(sub="u123", role="auditor", scopes=["read:finance"]))
    try:
        result = await agent_graph.ainvoke(...)
    finally:
        user_token_var.reset(token)

    # 下游任意节点 / 工具：
    tok = user_token_var.get()
    sql_executor.execute(f"SET app.current_user_id = {tok.sub}; ...")
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class UserToken:
    """代表一个已经验签通过的用户态 token。

    Attributes:
        sub:        OIDC subject（用户唯一 ID）
        role:       业务角色 auditor / finance / manager 等
        scopes:     细粒度权限 "read:finance" / "approve:expense" ...
        tenant:     多租户隔离 ID
        raw_jwt:    原始 JWT（透传到下游 DB session 设置）
        token_hash_prefix:  只留前 12 位 hash，供审计日志（不留原值）
    """

    sub: str
    role: str
    scopes: tuple[str, ...] = field(default_factory=tuple)
    tenant: str = "default"
    raw_jwt: str = ""
    token_hash_prefix: str = ""

    def has_scope(self, s: str) -> bool:
        return s in self.scopes


# 全局 ContextVar —— 默认 None，外层必须先 set
user_token_var: ContextVar[Optional[UserToken]] = ContextVar(
    "user_token_var", default=None
)


def current_user() -> UserToken:
    """在节点 / 工具代码里拿当前用户 token。未设置则 raise。"""
    tok = user_token_var.get()
    if tok is None:
        raise PermissionError(
            "当前上下文没有用户 token。上层必须先用 user_token_var.set(...) 注入。"
            "这是故意严格失败，避免『系统账号』悄悄接替用户身份执行。"
        )
    return tok


def require_scope(scope: str) -> None:
    """权限守卫：在工具入口处调用。"""
    tok = current_user()
    if not tok.has_scope(scope):
        raise PermissionError(f"用户 {tok.sub} 缺少必要权限：{scope}")
