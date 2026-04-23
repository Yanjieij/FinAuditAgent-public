"""版面分析（骨架 + 方案对比）。

**方案对比**（详见 docs/05_doc_rag_layout.md）：

    =========================  =================  ==========================
    方案                        中文效果            备注
    =========================  =================  ==========================
    PaddleOCR PP-StructureV2    ★★★★★（SOTA）      开源、TSR 表格结构识别
    LayoutLMv3（微调）          ★★★★              需要财务场景标注数据
    Unstructured.io hi_res      ★★★              英文强中文一般
    Adobe PDF Services API      ★★★★              商业、稳定、但计费
    MinerU / Marker             ★★★★              新兴开源、更新快
    =========================  =================  ==========================

本项目选择 **PaddleOCR PP-StructureV2**（在 environment.yml 里注释了，真实启用需要
打开对应依赖）。本文件给**接口骨架 + 模拟实现**，让 examples 能跑；生产替换成
真实的 PP-Structure 调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BlockKind = Literal["text", "title", "table", "figure", "formula", "kv"]


@dataclass
class LayoutBlock:
    """版面里的一个元素。"""

    kind: BlockKind
    page: int
    bbox: list[float]             # [x0, y0, x1, y1]
    text: str = ""
    # 只有 table 会填 cells；结构化的 {row, col, rowspan, colspan, text}
    cells: list[dict] = field(default_factory=list)
    # 只有 title 会填 level；用于构建 section_path
    title_level: int = 0


def analyze_pdf(pdf_path: str) -> list[LayoutBlock]:
    """对 PDF 做版面分析，返回所有 block。

    **骨架实现**：若 paddleocr 不可用，返回一个预置的 demo block 列表，
    方便 examples/05 跑通。生产替换为真实调用。
    """
    try:
        # 真实路径（需装 paddleocr+paddlepaddle）
        # from paddleocr import PPStructure
        # engine = PPStructure(layout=True, table=True, ocr=True, lang="ch")
        # result = engine(pdf_path)
        # 把 result 转成 LayoutBlock 列表
        raise ImportError("demo skeleton; real PaddleOCR path not wired")
    except ImportError:
        return _demo_blocks()


def _demo_blocks() -> list[LayoutBlock]:
    """一个假报销单的版面 block 列表，演示 KV 抽取与多区域。"""
    return [
        LayoutBlock(kind="title", page=1, bbox=[50, 30, 300, 60],
                    text="差旅费报销单", title_level=1),
        LayoutBlock(kind="kv", page=1, bbox=[50, 80, 400, 120],
                    text="申请人: 张三 | 部门: 市场部 | 日期: 2025-03-15"),
        LayoutBlock(
            kind="table", page=1, bbox=[50, 150, 550, 320],
            cells=[
                {"row": 0, "col": 0, "text": "费用类别"},
                {"row": 0, "col": 1, "text": "金额（元）"},
                {"row": 0, "col": 2, "text": "备注"},
                {"row": 1, "col": 0, "text": "机票"},
                {"row": 1, "col": 1, "text": "1800.00"},
                {"row": 1, "col": 2, "text": "国航 CA1234"},
                {"row": 2, "col": 0, "text": "酒店"},
                {"row": 2, "col": 1, "text": "920.00"},
                {"row": 2, "col": 2, "text": "两晚"},
                {"row": 3, "col": 0, "text": "合计"},
                {"row": 3, "col": 1, "text": "2720.00"},
                {"row": 3, "col": 2, "text": ""},
            ],
        ),
        LayoutBlock(kind="text", page=1, bbox=[50, 340, 550, 380],
                    text="出差事由：参加 2025 年春季客户大会，接触 3 位潜在客户。"),
        LayoutBlock(kind="kv", page=1, bbox=[50, 400, 300, 450],
                    text="申请人签字: 张三 | 部门经理签字: 李四"),
    ]
