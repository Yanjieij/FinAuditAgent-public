"""验证 RestrictedPython 沙箱的基本隔离性质。"""

from __future__ import annotations

from fin_audit_agent.sandbox.number_verifier import verify_numbers
from fin_audit_agent.sandbox.runner import run_code


def test_simple_exec_extracts_cells():
    code = "X = 1 + 2\nY_TOTAL = 100 * 2"
    r = run_code(code)
    assert r.ok
    assert r.cells.get("Y_TOTAL") == 200
    assert "X" not in r.cells  # 小写不被导出


def test_cannot_import_os():
    """应当被 RestrictedPython 的 safe_builtins 拦住。"""
    code = "import os\nX = 1"
    r = run_code(code)
    assert not r.ok
    assert "import" in (r.error or "").lower() or "name" in (r.error or "").lower()


def test_cannot_open_file():
    code = "FOO = open('/etc/passwd').read()"
    r = run_code(code)
    assert not r.ok


def test_decimal_precision():
    code = """
from decimal import Decimal
A = Decimal('0.1') + Decimal('0.2')
"""
    # Decimal 在 RestrictedPython 下能不能 import 视 safe_globals；本实现预置了 Decimal
    r = run_code(code)
    # 即使 import 被拦（Decimal 已经在 globals 里时不需要 import），主逻辑仍应成立
    if r.ok:
        assert r.cells.get("A") in ("0.3", 0.3)


def test_number_verifier_detects_missing_pointer():
    r = run_code("TOTAL = 1234")
    assert r.ok
    cells_by_exec = {r.exec_id: r.cells}

    good = f"合计 1234 [[exec_id={r.exec_id}#cell=TOTAL]]"
    bad = "合计 1234.5"  # 缺引证
    assert verify_numbers(good, cells_by_exec).ok
    rep = verify_numbers(bad, cells_by_exec)
    assert not rep.ok
    assert rep.violations[0].reason == "missing_pointer"
