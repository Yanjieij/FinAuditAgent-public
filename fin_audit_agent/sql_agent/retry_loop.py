"""Text-to-SQL 的容错重试闭环。

**流程**::

       generate_sql  ──►  validate (sqlglot)  ──►  dry_run (EXPLAIN)  ──►  execute
            │                   ✗                      ✗                      ✗
            │                    \\                    /                     /
            │                     └──► feedback → retry (上限 3 次)
            └─ LLM need_clarify? ──► 退出并把 clarify 问题冒泡给上层 Clarify 节点

**面试要点**：
    - 反馈给 LLM 的**不是原始 DB 错误**（太长、可能泄露 schema），而是**规整化后的错误类别 + hint**。
      例如 "列 ``abc`` 不存在（table=fact_expense 只有 [id, dept_id, amount, ...]）"
    - 上限 3 次是经验值；超过就降级 Clarify，别让 LLM 和 DB 互殴。
    - 每次重试前清空消息历史里上一次错误的 SQL，避免 LLM "记"住错写法。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Limits
from .executor import SqlExecutor
from .schema_indexer import SchemaIndex
from .schema_linker import LinkedSchema, link_schema
from .semantic_layer import SemanticLayer
from .sql_gen import SQLGenResult, generate_sql
from .validator import ValidateResult, dry_run_sql, validate


@dataclass
class RetryOutcome:
    """整个 retry_loop 的最终结果。"""

    ok: bool
    final_sql: str = ""
    attempts: int = 0
    clarify_question: str | None = None
    df: "object | None" = None  # pandas.DataFrame 或 None
    last_error: str = ""
    # 每次尝试的完整轨迹，供 lineage 与 observability 记录
    trace: list[dict] = None  # type: ignore[assignment]


def run(
    question: str,
    index: SchemaIndex,
    semantic: SemanticLayer,
    executor: SqlExecutor | None = None,
    *,
    llm=None,
    max_retries: int = Limits.SQL_MAX_RETRIES,
) -> RetryOutcome:
    """驱动 Text-to-SQL 全流程，带容错重试。

    注意：本函数**不做脱敏**。脱敏在上层图节点调用 :mod:`masker` 完成，
    这样 retry_loop 本身可以作为纯函数被测试。
    """
    executor = executor or SqlExecutor()
    linked = link_schema(question, index)
    trace: list[dict] = []
    last_error = ""

    for attempt in range(1, max_retries + 1):
        step: dict = {"attempt": attempt}

        # 1) 生成
        gen: SQLGenResult = generate_sql(
            question=_augment_with_feedback(question, last_error),
            linked=linked,
            semantic=semantic,
            llm=llm,
        )
        step["rationale"] = gen.rationale
        step["sql_raw"] = gen.sql

        # LLM 主动报需要澄清
        if gen.need_clarify:
            trace.append({**step, "verdict": "need_clarify"})
            return RetryOutcome(
                ok=False,
                attempts=attempt,
                clarify_question=gen.need_clarify,
                trace=trace,
            )

        # 2) 展开语义层占位符
        try:
            rendered = semantic.render(gen.sql)
        except KeyError as e:
            last_error = f"semantic_render_error: {e}"
            step["verdict"] = "semantic_err"
            trace.append(step)
            continue

        # 3) AST 校验 + LIMIT 注入
        v: ValidateResult = validate(rendered, default_limit=10_000)
        if not v.ok:
            last_error = f"validator_error: {v.reason}"
            step["verdict"] = "validate_err"
            trace.append(step)
            continue
        step["sql_validated"] = v.sql

        # 4) Dry run（EXPLAIN / LIMIT 0）验证执行期错误
        try:
            executor.execute(dry_run_sql(v.sql), timeout_sec=5)
        except Exception as e:
            last_error = f"dry_run_error: {e}"
            step["verdict"] = "dryrun_err"
            trace.append(step)
            continue

        # 5) 真正执行
        try:
            df = executor.execute(v.sql, timeout_sec=30)
        except Exception as e:
            last_error = f"exec_error: {e}"
            step["verdict"] = "exec_err"
            trace.append(step)
            continue

        step["verdict"] = "ok"
        step["rows"] = len(df)
        trace.append(step)
        return RetryOutcome(
            ok=True,
            final_sql=v.sql,
            attempts=attempt,
            df=df,
            trace=trace,
        )

    # 所有尝试都失败 → 降级 Clarify
    return RetryOutcome(
        ok=False,
        attempts=max_retries,
        last_error=last_error,
        clarify_question=(
            f"经过 {max_retries} 次尝试仍未能生成有效 SQL，最后的错误："
            f"{last_error}。能否用更具体的表/指标名再描述一下？"
        ),
        trace=trace,
    )


def _augment_with_feedback(question: str, last_error: str) -> str:
    """把上一次错误反馈拼到问题前面（LLM 自愈用）。"""
    if not last_error:
        return question
    return (
        f"{question}\n\n"
        f"---\n上一次生成的 SQL 未通过校验，错误：{last_error}\n请修正后重新生成。"
    )
