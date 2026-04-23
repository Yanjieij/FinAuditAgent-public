"""Langfuse 接入骨架。

**Langfuse vs LangSmith**：
    - **Langfuse**：开源，可**自托管**（国内部署不出网，合规友好）
    - **LangSmith**：SaaS，LangChain 原厂，功能最强
    - 本项目优先 Langfuse（金融场景合规敏感）

启用：在 .env 填 ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``。

核心用法::

    from fin_audit_agent.observability.langfuse_setup import init_langfuse, trace
    init_langfuse()

    @trace(name="sql_pipeline")
    def run_sql_stuff(...):
        ...
"""

from __future__ import annotations

from functools import wraps

from ..config import get_settings

_initialized = False


def init_langfuse() -> bool:
    """幂等初始化。返回是否成功初始化。"""
    global _initialized
    if _initialized:
        return True

    s = get_settings()
    if not s.langfuse_public_key or not s.langfuse_secret_key:
        return False

    try:
        from langfuse import Langfuse  # type: ignore

        Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
        _initialized = True
        return True
    except ImportError:
        return False


def trace(name: str | None = None):
    """装饰器：把函数调用记到 Langfuse trace。

    未初始化时降级为 no-op，不影响业务运行。
    """

    def deco(fn):
        try:
            from langfuse.decorators import observe  # type: ignore

            return observe(name=name or fn.__name__)(fn)
        except ImportError:
            @wraps(fn)
            def _pass(*args, **kwargs):
                return fn(*args, **kwargs)

            return _pass

    return deco
