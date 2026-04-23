"""Evidence-pointer 输出契约（★ 本项目的关键创新）。

**问题描述**：
    即使 Drafter 节点用了 tool-calling，它在写最终报告时仍可能"复述"数字——
    比如工具返回 1234.56，Drafter 在自然语言里写成 1234.5，就错了。
    金融零容忍场景下，我们需要一条硬约束：**凡是数字，必须能追到沙箱某次执行的某个 cell**。

**方案**：
    Drafter 的输出里，所有数字旁必须跟一个引用标签，例如：

        ``Q4 销售费用同比增长 15.3% [[exec_id=a1b2c3#cell=YOY_GROWTH]]``

    :func:`verify_numbers` 扫描文本，抽取所有"数字 + 可选百分号 + 可选单位"，
    逐个核对：
        1. 是否紧跟着 ``[[exec_id=...#cell=...]]`` 引用
        2. 引用的 exec_id 是否存在
        3. 引用的 cell 值是否和文本里的数字一致（允许格式化差异）

**面试可讲进阶**：
    - 可以叠加 LLM-as-judge 做语义核验（"本文档里关于 Q4 的数字是否全都 grounding 到 exec 数据"）
    - 可以把 verify_numbers 做成 LangGraph 的 Drafter 后置节点，失败就 retry

这个契约的本质是 **trust boundary** 下移：不信 LLM 的"记忆" / "复述"，只信沙箱的数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 抓"数字 + 可选单位 + 可选引用"
# 注意：不抓单独一个 0-9 的年份/编号，只抓包含小数点或千分位或百分号的"财务数字"
_NUM_PATTERN = re.compile(
    r"(?P<num>-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+%|-?\d+(?:\.\d+)?%)"
    r"(?P<unit>\s*(?:元|万元|亿元|%|USD|RMB|CNY))?"
    r"(?:\s*\[\[exec_id=(?P<exec_id>[a-f0-9]+)#cell=(?P<cell>[A-Z_][A-Z0-9_]*)\]\])?"
)


@dataclass
class Violation:
    """一次违规记录。"""

    number: str
    position: int
    reason: str  # "missing_pointer" | "unknown_exec" | "value_mismatch"


@dataclass
class VerifyReport:
    ok: bool
    violations: list[Violation]
    checked_count: int

    def render(self) -> str:
        """给 LLM 做 retry 提示时拼的一段反馈。"""
        if self.ok:
            return f"所有 {self.checked_count} 个数字都通过了 evidence-pointer 校验。"
        lines = [
            f"发现 {len(self.violations)} 处违规（共 {self.checked_count} 个数字）："
        ]
        for v in self.violations:
            lines.append(f"  - '{v.number}' @pos={v.position}: {v.reason}")
        lines.append("请修正：每个数字旁必须紧跟 `[[exec_id=XXX#cell=YYY]]`。")
        return "\n".join(lines)


def verify_numbers(
    text: str,
    cells_by_exec: dict[str, dict[str, Any]],
    *,
    tolerance: float = 1e-6,
) -> VerifyReport:
    """扫描 ``text`` 里的所有数字，核对 evidence-pointer 合规性。

    Args:
        text: Drafter 产生的最终报告文本
        cells_by_exec: ``{exec_id: {cell_name: value}}`` 所有已执行沙箱的变量映射。
            实际用时应由 graph.state 汇总各 ExecResult.cells 得到。
        tolerance: 浮点相对容差

    Returns:
        :class:`VerifyReport`，``ok=True`` 表示全通过。
    """
    violations: list[Violation] = []
    count = 0

    for m in _NUM_PATTERN.finditer(text):
        count += 1
        num_str = m.group("num")
        exec_id = m.group("exec_id")
        cell = m.group("cell")

        if exec_id is None or cell is None:
            violations.append(
                Violation(num_str, m.start(), "missing_pointer")
            )
            continue

        if exec_id not in cells_by_exec:
            violations.append(
                Violation(num_str, m.start(), f"unknown_exec:{exec_id}")
            )
            continue

        cells = cells_by_exec[exec_id]
        if cell not in cells:
            violations.append(
                Violation(num_str, m.start(), f"unknown_cell:{exec_id}#{cell}")
            )
            continue

        if not _values_match(num_str, cells[cell], tolerance):
            violations.append(
                Violation(
                    num_str,
                    m.start(),
                    f"value_mismatch: text='{num_str}' vs cell={cells[cell]!r}",
                )
            )

    return VerifyReport(
        ok=(not violations),
        violations=violations,
        checked_count=count,
    )


def _values_match(text_num: str, cell_val: Any, tol: float) -> bool:
    """把文本里的数字和 cell 真值比较。允许千分位、百分号、字符串化的 Decimal。"""
    try:
        # 清洗文本：去千分位、去单位
        cleaned = text_num.replace(",", "").replace("%", "")
        text_f = float(cleaned)
        cell_f = float(str(cell_val).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return False

    # 如果文本带 %，cell 常见是 0.xxx，放大 100 倍比较
    if text_num.endswith("%") and abs(cell_f) < 1:
        cell_f *= 100

    if cell_f == 0:
        return abs(text_f) < tol
    return abs(text_f - cell_f) / abs(cell_f) < tol
