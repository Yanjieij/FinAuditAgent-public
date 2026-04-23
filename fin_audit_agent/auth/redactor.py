"""进入 LLM context 前的最后一道脱敏。

和 :mod:`sql_agent.masker` 的区别：
    - ``sql_agent/masker.py`` 处理 **结果集的 DataFrame 列值**
    - 本文件处理 **任意文本里的潜在 PII**，包括：
        - 工具输出被拼到 prompt 的 JSON/Markdown 片段
        - 文档 RAG 抽出来的文本段
        - 用户问题本身（万一用户在问题里贴了身份证号）

**策略**：正则识别常见 PII 模式（身份证、手机号、银行卡、邮箱、IP），按等级掩码。

⚠️ 局限：
    - 正则不可能 100% 覆盖所有 PII
    - 对于被 BASE64 编码 / 加密 / 拆开多列拼的 PII 无能为力
    - 真实生产应叠加 **DLP（Data Loss Prevention）模型** + 列级规则 + 人工审计日志
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# 常见 PII 正则。注意 `\b` 在中文场景对边界判断不理想，所以手动用字符类
# 这些模式都偏保守，避免误伤
_PII_PATTERNS: list[tuple[str, str, int]] = [
    # 中国身份证 18 位
    ("id_card", r"\b\d{17}[\dXx]\b", 3),
    # 中国手机号
    ("phone", r"\b1[3-9]\d{9}\b", 2),
    # 银行卡（16-19 位）
    ("bank_card", r"\b\d{16,19}\b", 3),
    # 邮箱
    ("email", r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", 2),
    # IPv4
    ("ipv4", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", 1),
]


@dataclass
class RedactReport:
    """一次脱敏的报告，供审计日志记录。"""

    text: str  # 脱敏后的文本
    hits: list[tuple[str, int, int]]  # (kind, start, end) 原文本中命中的位置

    @property
    def hit_count(self) -> int:
        return len(self.hits)


def redact_text(text: str, min_level: int = 2) -> RedactReport:
    """对 ``text`` 做脱敏，返回脱敏后文本 + 命中位置。

    Args:
        text:       原文本
        min_level:  命中 pii 等级 >= 该值才脱敏。默认 2：掩码手机 / 邮箱 / 身份证。

    为什么同时返回 hits：
        审计要求"可解释"——哪段文字被谁因什么原因脱敏了，要能回放。
    """
    hits: list[tuple[str, int, int]] = []
    redacted = text

    for kind, pattern, lv in _PII_PATTERNS:
        if lv < min_level:
            continue
        offset = 0  # 每次 replace 后 text 长度可能变化；但我们改的是 redacted 不是 text
        for m in re.finditer(pattern, text):
            hits.append((kind, m.start(), m.end()))
        # 用 re.sub 做替换（需要新一轮扫描，避免 offset 跟踪复杂）
        redacted = re.sub(pattern, lambda mm, k=kind, lv=lv: _mask_match(mm.group(0), lv), redacted)

    return RedactReport(text=redacted, hits=hits)


def _mask_match(s: str, pii_level: int) -> str:
    """保留首尾少量字符，中间 *。身份证/卡号保留前 4 后 4；手机号保留前 3 后 4。"""
    if pii_level >= 3:
        if len(s) >= 8:
            return s[:4] + "*" * (len(s) - 8) + s[-4:]
        return "*" * len(s)
    if pii_level == 2:
        if "@" in s:  # 邮箱特殊处理
            local, _, domain = s.partition("@")
            return (local[:1] + "*" * max(1, len(local) - 1)) + "@" + domain
        if len(s) >= 7:
            return s[:3] + "*" * (len(s) - 7) + s[-4:]
        return "*" * len(s)
    return s
