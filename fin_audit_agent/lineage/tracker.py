"""数据血缘追踪器。

**核心数据结构**：有向图 `final_number → [sources]`。

每当 Drafter / Analyzer 把一个数字写进最终报告，就登记一条血缘：
    ``track(number=2720.0, sources=[{"kind": "exec", "exec_id": "abc", "cell": "TOTAL"}])``

查询时：用户点"2720.00" → ``resolve(2720.0)`` 返回所有 sources。

**实现**：
    - 简单内存图（每个请求一个 LineageTracker 实例；随 state 持久化）
    - 生产可换 Neo4j / OrientDB 做真正的审计图
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SourceKind = Literal["sql", "doc", "exec"]


@dataclass
class Source:
    """血缘的一端——数据的来源。"""

    kind: SourceKind
    # 三种 kind 对应的字段（用 dict 装，避免 Union 麻烦）
    details: dict[str, Any] = field(default_factory=dict)

    # 辅助构造
    @classmethod
    def sql(cls, sql_id: str, row: int | None = None, col: str | None = None):
        return cls(kind="sql", details={"sql_id": sql_id, "row": row, "col": col})

    @classmethod
    def doc(cls, chunk_id: str, page: int, bbox: list[float]):
        return cls(kind="doc", details={"chunk_id": chunk_id, "page": page, "bbox": bbox})

    @classmethod
    def exec_(cls, exec_id: str, cell: str):
        return cls(kind="exec", details={"exec_id": exec_id, "cell": cell})


@dataclass
class LineageRecord:
    """一条血缘：一个数字 → 一个或多个 source。"""

    number_key: str   # 为了查询友好，用字符串键（"revenue_q4" 或 "2720.00"）
    value: Any
    sources: list[Source] = field(default_factory=list)
    note: str = ""    # 可选说明："由 SQL A 汇总后在 exec B 里计算"


class LineageTracker:
    """一次请求的数据血缘追踪。随 AgentState 持久化。"""

    def __init__(self):
        self._records: dict[str, LineageRecord] = {}

    def track(self, key: str, value: Any, sources: list[Source], note: str = "") -> None:
        existing = self._records.get(key)
        if existing:
            existing.sources.extend(sources)
            if note:
                existing.note += (" | " + note if existing.note else note)
        else:
            self._records[key] = LineageRecord(
                number_key=key, value=value, sources=sources, note=note
            )

    def resolve(self, key: str) -> LineageRecord | None:
        return self._records.get(key)

    def all(self) -> list[LineageRecord]:
        return list(self._records.values())

    def to_json(self) -> list[dict]:
        return [
            {
                "number_key": r.number_key,
                "value": r.value,
                "sources": [
                    {"kind": s.kind, **s.details} for s in r.sources
                ],
                "note": r.note,
            }
            for r in self._records.values()
        ]


def render_lineage_for_cli(tracker: LineageTracker) -> str:
    """给 CLI 输出用的可读摘要。"""
    lines = ["## 数据血缘 Lineage"]
    for r in tracker.all():
        srcs = []
        for s in r.sources:
            if s.kind == "sql":
                srcs.append(f"SQL[{s.details.get('sql_id')}]@row={s.details.get('row')}#col={s.details.get('col')}")
            elif s.kind == "doc":
                srcs.append(f"DOC[{s.details.get('chunk_id')}]#p={s.details.get('page')}#bbox={s.details.get('bbox')}")
            elif s.kind == "exec":
                srcs.append(f"EXEC[{s.details.get('exec_id')}]#cell={s.details.get('cell')}")
        lines.append(f"- {r.number_key} = {r.value}  ← {', '.join(srcs)}")
    return "\n".join(lines)
