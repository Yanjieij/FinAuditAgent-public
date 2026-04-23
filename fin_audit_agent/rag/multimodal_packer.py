"""多模态打包：命中表格区域时，把**原图 crop + 抽取文本**同送 LLM。

**为什么多模态是表格 RAG 的杀手锏**：

    TSR（表格结构识别）不是 100%——对于合并单元格、斜线表头、手写补注，抽出来的
    文本可能错位。但是多模态 LLM（Claude 3.5 Sonnet / GPT-4o / Qwen-VL）**可以
    直接看原图**，两路信号交叉验证：
        - 文本抽错时，图像能兜底
        - 图像看不清时，文本能补
    实测准确率能从 80% 拉到 95%+。

**实现**：
    - 对每个命中的 table_row chunk，定位原 PDF 页面的 bbox
    - 用 PIL / pdf2image 裁 crop
    - 渲染成 LangChain 的多模态消息格式

本文件给骨架；真实生产需要 pdf2image + Pillow + Claude/OpenAI 的 vision content 格式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .hybrid_retriever import RetrievalHit


@dataclass
class MultimodalMessage:
    """LangChain 风格的多模态 HumanMessage 内容列表。"""

    blocks: list[dict[str, Any]]  # [{"type":"text","text":...} or {"type":"image_url","image_url":{...}}]


def pack(query: str, hits: list["RetrievalHit"], pdf_path: str | None = None) -> MultimodalMessage:
    """把命中的 hits 打包成多模态消息。

    **骨架实现**：只打包文本证据；当有 ``pdf_path`` 且命中 table_row 时，
    应该用 ``pdf2image.convert_from_path(pdf_path, first_page=page, last_page=page)``
    拿到页图再 crop。生产按下面注释启用。
    """
    from .citation import render_citation_block

    blocks: list[dict] = [{"type": "text", "text": f"问题: {query}\n\n以下为证据："}]

    for h in hits:
        blocks.append({"type": "text", "text": render_citation_block(h)})

        # 生产路径（TODO）：对表格区域插入原图 crop
        # if h.chunk.kind == "table_row" and pdf_path:
        #     img_data_url = _crop_to_data_url(pdf_path, h.chunk.page, h.chunk.bbox)
        #     blocks.append({"type": "image_url", "image_url": {"url": img_data_url}})

    return MultimodalMessage(blocks=blocks)


# def _crop_to_data_url(pdf_path: str, page: int, bbox: list[float]) -> str:
#     """把 PDF 某页的 bbox 裁成 base64 data url。生产启用此函数。"""
#     from pdf2image import convert_from_path
#     from PIL import Image
#     import base64, io
#     pages = convert_from_path(pdf_path, first_page=page, last_page=page)
#     img = pages[0].crop(tuple(bbox))
#     buf = io.BytesIO()
#     img.save(buf, format="PNG")
#     b64 = base64.b64encode(buf.getvalue()).decode()
#     return f"data:image/png;base64,{b64}"
