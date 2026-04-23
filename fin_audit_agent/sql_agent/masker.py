"""结果集级脱敏（进 LLM context 之前最后一道防线）。

**核心理念**（★ 面试讲点）：

    脱敏有两个时机——
    1. **查询计划时脱敏**：在 SQL 改写阶段，对 PII 列自动包 ``mask()``，DB 返回就是掩码值。
    2. **结果集后脱敏**：DB 正常返回原值，应用层按列标签二次掩码。

    本项目选 **"2 + 1"**：validator/linker 阶段就拒绝对高敏列直接做 SELECT *，
    但万一漏过，executor 返回后还会在这里兜底掩码，**保证 LLM 永远看不到身份证明文**。

**掩码策略**：
    - pii_level=0：不处理
    - pii_level=1：内部信息，保留
    - pii_level=2：手机号/姓名 → 保留首尾 1 位，中间 `*`
    - pii_level=3：身份证号/薪资 → 整体 `***`（或 K-anonymity 桶）

面试延伸：
    "为什么不是让 DB 视图做脱敏就够了？因为用户角色多，视图矩阵会爆炸；
     且生成式 Agent 场景下 LLM 可能把多列拼起来重构 PII，必须在**进 LLM 前**再防一层。"
"""

from __future__ import annotations

from typing import Any

from .schema_indexer import ColumnMeta, SchemaIndex


def mask_value(value: Any, pii_level: int) -> Any:
    """单个值的掩码策略。"""
    if value is None or pii_level == 0:
        return value
    s = str(value)
    if pii_level == 1:
        return s
    if pii_level == 2:
        if len(s) <= 2:
            return "*" * len(s)
        return s[0] + "*" * (len(s) - 2) + s[-1]
    if pii_level >= 3:
        return "***"
    return s


def mask_dataframe(df, columns_meta: dict[str, ColumnMeta]):
    """对 DataFrame 按列标签脱敏。

    Args:
        df: pandas DataFrame，结果集
        columns_meta: ``{column_name: ColumnMeta}``。如果 df 列名不在里，默认按 pii_level=0 不处理。

    为什么返回新的 DataFrame 而不是 inplace 改：
        脱敏是 **不可逆** 的，留住原始 df 便于审计日志（落 append-only log）。
    """
    masked = df.copy()
    for col in masked.columns:
        meta = columns_meta.get(col)
        if meta is None:
            continue
        if meta.pii_level <= 0:
            continue
        masked[col] = masked[col].map(lambda v, lv=meta.pii_level: mask_value(v, lv))
    return masked


def columns_meta_for_sql(sql: str, index: SchemaIndex) -> dict[str, ColumnMeta]:
    """粗略解析 SELECT 列表，反查每列的 PII 等级。

    本 demo 只处理 ``SELECT col1, col2, ...`` 的平凡情况；
    生产版本应该用 sqlglot 做完整 AST 遍历 + alias 追踪。
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return {}

    result: dict[str, ColumnMeta] = {}
    tree = sqlglot.parse_one(sql)

    for col_exp in tree.find_all(exp.Column):
        tbl = col_exp.table
        col = col_exp.name
        if not tbl:
            # 无限定符的列，只能退化遍历所有表找同名
            for t in index.all_tables():
                for c in index.columns_of(t.name):
                    if c.name == col:
                        result[col] = c
                        break
                if col in result:
                    break
        else:
            for c in index.columns_of(tbl):
                if c.name == col:
                    result[col] = c
                    break

    return result
