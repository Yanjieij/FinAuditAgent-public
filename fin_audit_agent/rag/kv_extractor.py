"""报销单 Key-Value 抽取（骨架）。

**方案对比**：
    - **LayoutLMv3 微调**：效果最好，但需要几百张已标注样本
    - **Donut（零样本）**：不需要标注，但中文效果弱，速度慢
    - **规则 + OCR**：最快上手，适合模板稳定的单据

本 demo 使用**规则 + 版面 block**：把 ``kind=kv`` 的 block 按 `"key1: v1 | key2: v2"`
的格式解析。真实场景替换为 LayoutLMv3 推理。

**大小写金额交叉校验**（这是报销单场景的硬需求）：
    同一个金额要么是 ``¥1234.00``，要么是 "壹仟贰佰叁拾肆元整"，两路都抽出来对比。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .layout import LayoutBlock


@dataclass
class ExpenseKV:
    applicant: str | None = None
    department: str | None = None
    date: str | None = None
    total_amount: float | None = None
    total_amount_in_words: str | None = None
    purpose: str | None = None
    signatures: list[str] = None  # type: ignore[assignment]

    def cross_check(self) -> list[str]:
        """返回所有交叉校验的告警（empty 表示都通过）。"""
        issues: list[str] = []
        if self.total_amount is not None and self.total_amount_in_words:
            if not _amount_matches_words(self.total_amount, self.total_amount_in_words):
                issues.append(
                    f"大小写金额不一致：{self.total_amount} vs {self.total_amount_in_words}"
                )
        return issues


def extract(blocks: list[LayoutBlock]) -> ExpenseKV:
    """从 layout 结果里抽 KV。"""
    kv = ExpenseKV(signatures=[])
    for b in blocks:
        if b.kind == "kv":
            _parse_kv_line(b.text, kv)
        elif b.kind == "table":
            _extract_total_from_table(b, kv)
        elif b.kind == "text":
            if any(w in b.text for w in ("事由", "用途", "说明")):
                kv.purpose = (kv.purpose or "") + b.text
    return kv


_KV_SEP = re.compile(r"[|｜,，]")


def _parse_kv_line(text: str, kv: ExpenseKV) -> None:
    """拆 "申请人: 张三 | 部门: 市场部" 这样的行。"""
    for pair in _KV_SEP.split(text):
        if ":" not in pair and "：" not in pair:
            continue
        pair = pair.replace("：", ":")
        k, _, v = pair.partition(":")
        k, v = k.strip(), v.strip()
        if "申请人" in k:
            kv.applicant = v
        elif "部门" in k:
            kv.department = v
        elif "日期" in k:
            kv.date = v
        elif "签字" in k or "签名" in k:
            kv.signatures.append(f"{k}={v}")


def _extract_total_from_table(block: LayoutBlock, kv: ExpenseKV) -> None:
    """从表格里抽"合计"行的金额。"""
    # 合计行常在第一列是 "合计"，找对应金额列
    for c in block.cells:
        if c.get("text") == "合计":
            row = c["row"]
            # 找该行里能解析成数字的 cell
            for c2 in block.cells:
                if c2["row"] == row and c2 is not c:
                    try:
                        kv.total_amount = float(c2["text"].replace(",", ""))
                        return
                    except (TypeError, ValueError):
                        continue


def _amount_matches_words(amount: float, words: str) -> bool:
    """大小写金额简单比较。这是 demo 版本——真实实现需要完整的中文数字解析。"""
    # 这里只做 smoke 检测：words 里至少应该提到整数部分主要位
    int_part = int(amount)
    main_digit_words = {0: "零", 1: "壹", 2: "贰", 3: "叁", 4: "肆",
                         5: "伍", 6: "陆", 7: "柒", 8: "捌", 9: "玖"}
    first_digit = int(str(int_part)[0])
    return main_digit_words[first_digit] in words
