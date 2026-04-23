"""语义切分：按章节 + 表格单元切，不按字符。

**对比传统 RecursiveCharacterTextSplitter**：

    +----------------------+--------------------------+-------------------------+
    | 维度                 | RecursiveCharacterText   | 本项目的 SemanticChunker |
    +======================+==========================+=========================+
    | 切分依据              | chunk_size + overlap     | 章节 / 表格单元           |
    | 表格处理              | 切碎丢层级                | 整表或整行为一个 chunk    |
    | chunk 元数据          | 无                       | page + bbox + section   |
    | RAG 引证              | 只能贴文本                | 可贴原图 crop             |
    +----------------------+--------------------------+-------------------------+

**实现**：
    - 顺序扫 blocks
    - 维护 "section stack"（按 title.level 入/出栈）得到 ``section_path``
    - 每个 block 单独成 chunk；table 作为整体，必要时按行拆（行内不再切）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .layout import LayoutBlock
from .table_extractor import cells_to_json


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    page: int
    bbox: list[float]
    section_path: str
    kind: str            # "text" | "table_row" | "kv"
    text: str            # 给 retrieval 匹配用的文本
    meta: dict = field(default_factory=dict)


def chunk_blocks(doc_id: str, blocks: list[LayoutBlock]) -> list[Chunk]:
    """把 layout blocks 切成 semantic chunks。"""
    chunks: list[Chunk] = []
    section_stack: list[str] = []

    for idx, b in enumerate(blocks):
        # 标题维护 section stack
        if b.kind == "title":
            # 出栈到合适层级
            while len(section_stack) >= b.title_level:
                section_stack.pop()
            section_stack.append(b.text)
            # 标题本身也作为 chunk 索引的一部分（方便用户问"XX 一节写了啥"）
            chunks.append(Chunk(
                chunk_id=f"{doc_id}:{idx}",
                doc_id=doc_id,
                page=b.page,
                bbox=b.bbox,
                section_path=" > ".join(section_stack),
                kind="title",
                text=b.text,
            ))
            continue

        section_path = " > ".join(section_stack)

        if b.kind == "table":
            # 表格按"每行 + 表头"作为一个 chunk，保留层级
            tj = cells_to_json(b.cells)
            header_str = " | ".join(tj.headers)
            for r_i, row in enumerate(tj.rows):
                row_str = " | ".join(row)
                chunks.append(Chunk(
                    chunk_id=f"{doc_id}:{idx}:r{r_i}",
                    doc_id=doc_id,
                    page=b.page,
                    bbox=b.bbox,
                    section_path=section_path,
                    kind="table_row",
                    text=f"{header_str}\n{row_str}",
                    meta={"row_in_table": r_i,
                          "section_tag": tj.section_paths[r_i]},
                ))
            continue

        # text / kv / figure / formula
        chunks.append(Chunk(
            chunk_id=f"{doc_id}:{idx}",
            doc_id=doc_id,
            page=b.page,
            bbox=b.bbox,
            section_path=section_path,
            kind=b.kind,
            text=b.text,
        ))

    return chunks
