"""Example 01 —— 零幻觉 + Evidence-pointer 契约。

跑::

    conda activate fin-audit-agent
    python examples/01_sandbox_number_verifier.py

演示 3 件事：
    1. ``sandbox.runner.run_code`` 在 RestrictedPython 里算数，返回结构化 ExecResult
    2. 沙箱里抽大写命名的变量作为 cells，Drafter 可引用
    3. ``number_verifier.verify_numbers`` 对"带/不带 evidence-pointer"的文本做契约校验
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from fin_audit_agent.sandbox.number_verifier import verify_numbers
from fin_audit_agent.sandbox.runner import run_code

console = Console()


def main() -> None:
    # 1. 在沙箱里算几个财务数字
    code = """
import pandas as pd
from decimal import Decimal

# 模拟费用数据（注意：沙箱里预置了 pd / np / Decimal，但没 os / subprocess）
df = pd.DataFrame({
    "category": ["营销", "差旅", "办公", "营销"],
    "amount":   [Decimal("12000.50"), Decimal("3400.00"),
                 Decimal("890.10"),  Decimal("5100.00")],
})

# === 用全大写变量导出"cells"（约定） ===
TOTAL_AMOUNT = sum(df["amount"])                               # 21390.60
MARKETING_AMOUNT = sum(df.loc[df["category"] == "营销", "amount"])  # 17100.50
NON_MARKETING_AMOUNT = TOTAL_AMOUNT - MARKETING_AMOUNT              # 4290.10
"""

    result = run_code(code, timeout_sec=5)
    console.print(f"[green]exec_id:[/green] {result.exec_id}")
    console.print(f"[green]ok:[/green] {result.ok}")

    if not result.ok:
        console.print(f"[red]error:[/red] {result.error}")
        return

    tbl = Table(title="抽取到的 cells（将作为 Drafter 引证源）")
    tbl.add_column("cell")
    tbl.add_column("value")
    for k, v in result.cells.items():
        tbl.add_row(k, str(v))
    console.print(tbl)

    # 2. 模拟 Drafter 可能输出的两种草稿：合规 / 不合规
    cells_by_exec = {result.exec_id: result.cells}

    good_draft = (
        f"本月总费用 {result.cells['TOTAL_AMOUNT']} "
        f"[[exec_id={result.exec_id}#cell=TOTAL_AMOUNT]]，"
        f"其中营销费用占比 79.94%，属于正常范围。"
    )
    # 上面这份 "79.94%" 没有引证，会被 verifier 抓到

    bad_draft = (
        f"本月总费用 21390.60 元，营销费用 17100.50 元，非营销类 4290.10。"
    )

    for name, draft in [("good 草稿", good_draft), ("bad  草稿", bad_draft)]:
        console.rule(f"[bold]{name}[/bold]")
        report = verify_numbers(draft, cells_by_exec)
        console.print(f"  draft: {draft}")
        console.print(f"  verify.ok = {report.ok}  checked = {report.checked_count}")
        if not report.ok:
            for v in report.violations:
                console.print(f"  [red]violation[/red]: '{v.number}' @pos={v.position} reason={v.reason}")


if __name__ == "__main__":
    main()
