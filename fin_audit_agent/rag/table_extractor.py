"""把 TSR（表格结构识别）的 cells 转成**层级 JSON + Markdown**。

**为什么不 flatten**（面试要讲透）：

    传统做法把 cells 拼成 ``"类别 金额 备注\\n机票 1800 CA1234\\n..."`` 的纯文本，
    LLM 容易**丢失表头层级**，比如合并资产负债表的"资产 > 流动资产 > 应收账款"。

    正确做法：
        1. 保留每个单元的 ``(row, col, rowspan, colspan, text)``
        2. 识别哪几行是表头（TSR 一般给 ``row_type``；若没有，按"是否加粗/是否含数字"启发式）
        3. 构造"行路径" / "列路径"（"资产 > 流动资产 > 应收账款"）
        4. 输出 Markdown 表 **+** 每行的 section_path

这样 LLM 既能看到平面表也能读到层级；retrieval 时表头路径也能作为 key 匹配。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TableJSON:
    headers: list[str]
    rows: list[list[str]]
    section_paths: list[str]  # 每行的层级路径，长度与 rows 等

    def to_markdown(self) -> str:
        """渲染成 GFM 表格。"""
        if not self.rows:
            return "(empty table)"
        header_line = "| " + " | ".join(self.headers) + " |"
        sep = "| " + " | ".join("---" for _ in self.headers) + " |"
        body = ["| " + " | ".join(r) + " |" for r in self.rows]
        return "\n".join([header_line, sep, *body])


def cells_to_json(cells: list[dict[str, Any]]) -> TableJSON:
    """把 TSR cells 转成结构化 TableJSON。

    简化实现（demo 用）：
        - 第一行作为 headers
        - 其余行作为 body
        - section_paths：如果某行第一列包含"合计"/"小计"，path 打上标记；否则为 ""
    """
    if not cells:
        return TableJSON(headers=[], rows=[], section_paths=[])

    max_row = max(c["row"] for c in cells)
    max_col = max(c["col"] for c in cells)

    grid: list[list[str]] = [
        ["" for _ in range(max_col + 1)] for _ in range(max_row + 1)
    ]
    for c in cells:
        grid[c["row"]][c["col"]] = c.get("text", "")

    headers = grid[0]
    body = grid[1:]
    paths = []
    for r in body:
        first = (r[0] if r else "") or ""
        if "合计" in first or "小计" in first:
            paths.append("（汇总行）")
        else:
            paths.append("")

    return TableJSON(headers=headers, rows=body, section_paths=paths)
