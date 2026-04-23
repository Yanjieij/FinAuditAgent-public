"""OPA Rego 策略引擎（骨架）。

**为什么把权限策略做成 OPA**（面试讲点）：

    传统做法：权限判断散落在业务代码里（``if role=='admin': ...``），
    改规则要改代码、改代码要发版、发版要经过一堆流程——**不敏捷且容易漏**。

    OPA（Open Policy Agent）是 CNCF 孵化的通用策略引擎，用 **Rego** DSL 写规则。
    策略单独部署、单独灰度、单独审计。Agent 里只调 ``policy_engine.allow(input)``。

**用例**：审批金额阈值 / 谁能打款 / 哪些列对哪些角色可见。

---

本文件只给接口与本地 fallback（不依赖 OPA server）：
    - ``allow(action, subject, resource)`` 在本地按一组内置规则判断
    - 生产换成 HTTP 调 OPA sidecar：``POST http://opa:8181/v1/data/fin/allow``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class PolicyInput:
    """OPA 风格的策略判断输入。"""

    subject: dict[str, Any]   # {"role": "auditor", "sub": "u123", ...}
    action: str               # "approve:expense" / "read:salary" / ...
    resource: dict[str, Any]  # {"kind": "expense", "amount": 12345, ...}


# 内置规则注册表：{action: fn(PolicyInput) -> bool}
# 生产换成 OPA server 调用；这里给 demo / 离线策略用
_LOCAL_RULES: dict[str, Callable[[PolicyInput], bool]] = {}


def rule(action: str):
    """装饰器：注册本地策略函数。"""

    def deco(fn: Callable[[PolicyInput], bool]) -> Callable[[PolicyInput], bool]:
        _LOCAL_RULES[action] = fn
        return fn

    return deco


def allow(action: str, subject: dict[str, Any], resource: dict[str, Any]) -> bool:
    """判断是否允许。默认拒绝（deny-by-default）。"""
    fn = _LOCAL_RULES.get(action)
    if fn is None:
        return False  # 未定义 = 拒绝
    return fn(PolicyInput(subject=subject, action=action, resource=resource))


# ---------------------------------------------------------------------------
# 预置几条 demo 规则（examples 会用）
# ---------------------------------------------------------------------------
@rule("approve:expense")
def _approve_expense(p: PolicyInput) -> bool:
    """审批权限阶梯：
        - amount <=  10k：preparer 角色即可
        - amount <= 100k：approver 或 cfo
        - amount  > 100k：仅 cfo
    """
    amt = float(p.resource.get("amount", 0))
    role = p.subject.get("role", "")
    if amt <= 10_000:
        return role in {"preparer", "approver", "cfo"}
    if amt <= 100_000:
        return role in {"approver", "cfo"}
    return role == "cfo"


@rule("read:salary")
def _read_salary(p: PolicyInput) -> bool:
    """读薪资：只有 HR 或 CFO 角色允许。"""
    return p.subject.get("role") in {"hr", "cfo"}


@rule("export:csv")
def _export_csv(p: PolicyInput) -> bool:
    """导出 CSV：仅 auditor / cfo 允许，且需 scope ``export:data``。"""
    role = p.subject.get("role")
    scopes = set(p.subject.get("scopes", ()))
    return role in {"auditor", "cfo"} and "export:data" in scopes
