"""列级 PII / 敏感性打标。

**为什么要有专门的 tagger**（而不是把 pii_level 直接写在 schema 里）：

    schema 是 DBA 维护的，但 PII 等级往往由**安全团队**按合规要求另行评估。
    把打标独立成一个文件/配置文件，让两组人可以并行改、各自 code review，
    并且能做**合规审计**（"谁把这列从 PII=3 降到 PII=2 的？"）。

本文件既给一个纯内存的 tagger，也给一个把标签反向写回 SchemaIndex 的工具函数，
真实场景会从 ``config/pii_tags.yaml`` 加载。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..sql_agent.schema_indexer import SchemaIndex


# 通用规则：列名模糊匹配 → pii_level
# 面试可讲：这只是兜底，真实公司会由 Data Governance 团队维护白名单
_NAME_HEURISTICS: list[tuple[str, int]] = [
    ("id_card", 3),
    ("idcard", 3),
    ("ssn", 3),
    ("salary", 3),
    ("passport", 3),
    ("bank_account", 3),
    ("phone", 2),
    ("mobile", 2),
    ("email", 2),
    ("name", 2),
    ("address", 2),
    ("birth", 2),
]


@dataclass
class TagRule:
    table: str
    column: str
    pii_level: int


def heuristic_level(column_name: str) -> int:
    """按名字做粗略推断。生产不要只靠这个！"""
    lower = column_name.lower()
    for pat, lv in _NAME_HEURISTICS:
        if pat in lower:
            return lv
    return 0


def apply_rules(index: SchemaIndex, rules: list[TagRule]) -> None:
    """把显式规则 upsert 回 SchemaIndex。冲突时显式规则覆盖启发式。"""
    for t in index.all_tables():
        for c in index.columns_of(t.name):
            # 先按启发式给一个兜底等级
            lv = max(c.pii_level, heuristic_level(c.name))
            # 再看是否有显式规则命中
            for r in rules:
                if r.table == t.name and r.column == c.name:
                    lv = r.pii_level
                    break
            if lv != c.pii_level:
                c.pii_level = lv
                index.upsert_column(c)
    index.commit()
