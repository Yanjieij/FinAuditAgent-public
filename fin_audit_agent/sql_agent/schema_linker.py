"""Schema Linking —— 把全库 schema 压缩成与问题相关的小子集。

**算法**：
    1. 用户问题 → jieba 分词抽出候选实体
    2. 对候选实体：
       a. 精确/模糊匹配 :class:`~.schema_indexer.GlossaryEntry.term` → 命中该术语的表
       b. 关键词 BM25 命中表/列的 description
       c. （可选）用 embedding 做语义相似度召回
    3. 将三路结果用 RRF 融合，取 top-K 张表 + 每张表 top-K 列

**输出 prompt 片段**（喂给 sql_gen.py）::

    ## 相关表与列
    ### fact_expense（费用事实表）
    - id (INT): 费用单号
    - dept_id (INT): 部门 ID
    - amount (DECIMAL): 报销金额（元）
    - category (TEXT): 费用类别 差旅/办公/营销
    - occurred_at (DATE): 发生日期
    ### dim_department（部门维度表）
    - ...
    ## 业务术语
    - "部门超支" ≈ sum(expense.amount) - sum(department.budget) per period

**面试要点**：
    - 压 prompt 之外，**实体未命中也是重要信号** → 进 Clarify 节点反问用户
    - 对极大库（>1000 张表），先做领域过滤（财务/HR/销售），避免跨域召回噪声
    - 这里用 BM25 + 字面模糊；生产可加 bge-m3 稠密检索做 RRF
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema_indexer import ColumnMeta, SchemaIndex, TableMeta


@dataclass
class LinkedSchema:
    """Schema Linking 的结果。"""

    tables: list["TableMeta"]
    columns: dict[str, list["ColumnMeta"]]  # table -> columns
    glossary: list[tuple[str, str]]  # (term, formula)

    def to_prompt_block(self) -> str:
        """渲染成 prompt 里可直接贴的 Markdown 片段。"""
        lines = ["## 相关表与列"]
        for t in self.tables:
            lines.append(f"### {t.name}（{t.description}）")
            for c in self.columns.get(t.name, []):
                # 带 PII 标记（但不暴露原值样例，样例只限非 PII）
                tag = f"[PII={c.pii_level}]" if c.pii_level > 0 else ""
                lines.append(f"- {c.name} ({c.dtype}) {tag}: {c.description}")

        if self.glossary:
            lines.append("\n## 业务术语")
            for term, formula in self.glossary:
                lines.append(f'- "{term}" ≈ {formula}')
        return "\n".join(lines)


def link_schema(
    question: str,
    index: "SchemaIndex",
    top_tables: int = 4,
    top_cols_per_table: int = 8,
) -> LinkedSchema:
    """把 ``question`` 压缩出相关的表/列子集。

    实现是 demo 级别的 BM25 + 字面匹配：
        - 抽取问题里所有 2+ 字的中文词 / 字母词
        - 用这些词去对比 glossary.term / tables.description / columns.description
        - 评分：命中 term 权重 3，命中 table.description 权重 2，命中 col.description 权重 1

    生产版本应该：
        - 分词用 jieba + 自定义金融词典
        - 描述用 bge-m3 嵌入 + Chroma 做 ANN
        - 最后一路 RRF 融合
    """
    tokens = _tokenize_zh(question)

    # 打分：table_name -> score
    table_scores: dict[str, float] = {c.name: 0.0 for c in index.all_tables()}
    hit_glossary: list[tuple[str, str]] = []

    # 先过 glossary —— 业务术语命中最强烈
    for g in index.all_glossary():
        if _any_in(g.term, tokens):
            hit_glossary.append((g.term, g.formula))
            for t in g.tables:
                table_scores[t] = table_scores.get(t, 0) + 3.0

    # 再过 table 描述
    for t in index.all_tables():
        if _any_in(t.description, tokens):
            table_scores[t.name] = table_scores.get(t.name, 0) + 2.0

    # 列描述命中也给对应表加分
    all_cols_by_table: dict[str, list["ColumnMeta"]] = {
        t.name: index.columns_of(t.name) for t in index.all_tables()
    }
    for tbl, cols in all_cols_by_table.items():
        for c in cols:
            if _any_in(c.description, tokens):
                table_scores[tbl] = table_scores.get(tbl, 0) + 1.0

    # 选 top_tables
    picked = sorted(table_scores.items(), key=lambda kv: kv[1], reverse=True)
    picked_tables_meta = []
    picked_cols: dict[str, list["ColumnMeta"]] = {}
    for name, score in picked[:top_tables]:
        if score <= 0:
            continue
        t_meta = next(t for t in index.all_tables() if t.name == name)
        picked_tables_meta.append(t_meta)

        # 列也排：命中 tokens 的列排前，再按 dtype 简单排序
        cols = all_cols_by_table[name]
        cols_sorted = sorted(
            cols,
            key=lambda c: (0 if _any_in(c.description, tokens) else 1, c.name),
        )
        picked_cols[name] = cols_sorted[:top_cols_per_table]

    return LinkedSchema(
        tables=picked_tables_meta,
        columns=picked_cols,
        glossary=hit_glossary,
    )


# ---------------------------------------------------------------------------
# 分词 / 匹配工具
# ---------------------------------------------------------------------------
def _tokenize_zh(text: str) -> list[str]:
    """中文+英文 token 化。懒加载 jieba 避免强依赖。"""
    try:
        import jieba

        return [t for t in jieba.cut(text) if len(t) >= 2 and t.strip()]
    except ImportError:
        # 退化：按连续中文 / 字母数字切
        import re

        return [t for t in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text) if len(t) >= 2]


def _any_in(target: str | None, tokens: list[str]) -> bool:
    """任一 token 出现在 target 里（子串匹配）。"""
    if not target:
        return False
    return any(tok in target for tok in tokens)
