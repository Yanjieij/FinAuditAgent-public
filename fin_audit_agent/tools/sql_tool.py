"""SQL 工具：LLM 触发 Text-to-SQL 查数。

调用路径：tool 入参 = 自然语言问题 → retry_loop.run → DataFrame → masker → JSON 返回。
"""

from __future__ import annotations

import json

from ..auth.audit_log import AuditLog, hash_text
from ..auth.injection_guard import wrap_untrusted
from ..auth.token_context import current_user, require_scope
from ..sql_agent.executor import SqlExecutor
from ..sql_agent.masker import columns_meta_for_sql, mask_dataframe
from ..sql_agent.retry_loop import run as sql_run
from ..sql_agent.schema_indexer import SchemaIndex
from ..sql_agent.semantic_layer import SemanticLayer


def sql_query_tool(
    question: str,
    *,
    schema_index: SchemaIndex | None = None,
    semantic: SemanticLayer | None = None,
) -> str:
    """用自然语言查数。

    **权限**：需 ``read:finance`` scope；薪资等敏感列由 masker 在结果集上再脱敏。
    """
    require_scope("read:finance")
    user = current_user()

    schema_index = schema_index or SchemaIndex()
    semantic = semantic or SemanticLayer.demo()

    outcome = sql_run(question, schema_index, semantic, executor=SqlExecutor())

    if not outcome.ok:
        reply = {
            "ok": False,
            "clarify_question": outcome.clarify_question,
            "attempts": outcome.attempts,
        }
        _audit(user, question, outcome, reply)
        return wrap_untrusted(json.dumps(reply, ensure_ascii=False), source="sql")

    # 结果集脱敏
    col_meta = columns_meta_for_sql(outcome.final_sql, schema_index)
    import pandas as pd  # 必须在真实数据路径里才 import
    masked_df = mask_dataframe(outcome.df, col_meta) if isinstance(outcome.df, pd.DataFrame) else outcome.df

    reply = {
        "ok": True,
        "sql": outcome.final_sql,
        "rows": len(masked_df),
        "columns": list(masked_df.columns),
        "preview": masked_df.head(20).to_dict("records"),
        "masked_columns": [c for c, m in col_meta.items() if m.pii_level >= 2],
    }
    _audit(user, question, outcome, reply)
    return wrap_untrusted(json.dumps(reply, ensure_ascii=False, default=str), source="sql")


def _audit(user, question, outcome, reply) -> None:
    try:
        AuditLog().append(
            user=user,
            action="sql.query",
            input_hash=hash_text(question),
            output_hash=hash_text(str(reply)[:500]),
            metadata={"attempts": outcome.attempts, "ok": outcome.ok},
        )
    except Exception:
        pass
