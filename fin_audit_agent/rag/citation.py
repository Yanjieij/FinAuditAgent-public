"""Cited retrieval：把 chunk 转成 LLM 看的引证格式。

**格式约定**（LLM 必须按此输出）::

    ... 根据合并资产负债表的流动资产项 [citation:DOC001#page=3#bbox=50,120,420,180] ...

**评测层面**：
    - ``citation_exact_match``：LLM 引证的 doc_id / page 是否和原 chunk 一致
    - ``citation_bbox_iou``：bbox 重叠度 > 0.5 视为引用准确

**为什么带 bbox**：审计场景用户点引证 → 系统定位到 PDF 页 → 框出原位置 → 肉眼复核。
这是审计合规的"soft requirement"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hybrid_retriever import RetrievalHit


def render_citation_block(hit: "RetrievalHit") -> str:
    """渲染成 prompt 里的一段带引证标签的材料。

    最终送给 LLM 的材料形如::

        <evidence id="DOC001:3:r2" page="3" bbox="50,120,420,180" section="合并资产负债表">
        应收账款 1,234,567.89 1,100,000.00
        </evidence>
    """
    c = hit.chunk
    bbox_str = ",".join(f"{x:.0f}" for x in c.bbox)
    return (
        f'<evidence id="{c.chunk_id}" page="{c.page}" '
        f'bbox="{bbox_str}" section="{c.section_path}">\n'
        f"{c.text}\n"
        "</evidence>"
    )


# ----------------------------------------------------------------------------
# 引证合规解析
# ----------------------------------------------------------------------------
_CITE_PATTERN = re.compile(
    r"\[citation:(?P<id>[\w\-.:]+)#page=(?P<page>\d+)"
    r"(?:#bbox=(?P<bbox>[\d.,]+))?\]"
)


@dataclass
class CitationRef:
    chunk_id: str
    page: int
    bbox: tuple[float, ...] | None


def parse_citations(text: str) -> list[CitationRef]:
    refs: list[CitationRef] = []
    for m in _CITE_PATTERN.finditer(text):
        bbox_str = m.group("bbox")
        bbox = tuple(float(x) for x in bbox_str.split(",")) if bbox_str else None
        refs.append(CitationRef(
            chunk_id=m.group("id"),
            page=int(m.group("page")),
            bbox=bbox,
        ))
    return refs


def bbox_iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """两个 bbox 的 IoU（交并比）。用于 citation 准确率评测。"""
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0
