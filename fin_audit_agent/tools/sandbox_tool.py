"""沙箱工具：LLM 可调用的 ``run_python``。

Agent 决定要算什么 → 生成一段 Python 代码 → 这个工具在沙箱里执行并返回结构化结果。
LLM 拿到 ``exec_id``，在后续报告中必须用 ``[[exec_id=XXX#cell=YYY]]`` 引证具体数字。
"""

from __future__ import annotations

from typing import Any

from ..auth.audit_log import AuditLog, hash_text
from ..auth.injection_guard import wrap_untrusted
from ..auth.token_context import current_user, require_scope
from ..config import get_settings
from ..sandbox.runner import run_code


def run_python(code: str, inputs: dict[str, Any] | None = None) -> str:
    """在本地 RestrictedPython 沙箱里跑一段代码，返回给 LLM 的摘要字符串。

    Args:
        code: 待执行的 Python 代码
        inputs: 可选注入变量（如上游 SQL 工具返回的 DataFrame）

    Returns:
        JSON 字符串（用 ``<tool_result untrusted>`` 包裹）。包含 exec_id / cells / artifacts。

    **权限**：需 ``compute:sandbox`` scope。
    """
    require_scope("compute:sandbox")
    user = current_user()
    settings = get_settings()

    result = run_code(code, inputs=inputs, timeout_sec=settings.sandbox_timeout_sec)

    # 审计日志
    try:
        AuditLog().append(
            user=user,
            action="sandbox.run",
            input_hash=hash_text(code),
            output_hash=hash_text(result.summary()),
            metadata={
                "exec_id": result.exec_id,
                "ok": result.ok,
                "wall_time_ms": result.wall_time_ms,
            },
        )
    except Exception:
        # 审计失败不阻断主流程，但要留痕——这里真实环境要告警
        pass

    # 打包返回给 LLM：只给必要信息，明确 untrusted
    import json
    payload = {
        "exec_id": result.exec_id,
        "ok": result.ok,
        "cells": result.cells,
        "stdout": result.stdout[:2000],
        "error": result.error,
        "artifacts": [
            {"kind": a.kind, "path": a.path, "preview": a.preview}
            for a in result.artifacts
        ],
    }
    return wrap_untrusted(json.dumps(payload, ensure_ascii=False, default=str), source="sandbox")
