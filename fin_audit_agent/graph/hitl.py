"""HITL（Human-in-the-loop）审批工具。

**工作流程**：

    1. ``Drafter`` 节点产出 ``state["draft"]``（含数字的报告）
    2. 进入 ``HumanReview`` 节点前，``graph.compile(interrupt_before=["execute"])``
       会让图**停住**，checkpointer 落盘当前 state
    3. 调 :func:`build_approval_payload` 构造 **HMAC 签名的审批载荷**，发飞书/企微
    4. 审批人点"同意"触发 webhook → 后端校验签名 → 调 ``graph.update_state`` 把
       ``approval_status`` 改成 ``approved`` → ``graph.invoke(None, config)`` 继续
    5. 超时由定时任务扫 pending checkpoint，超过 24h 自动置 ``timeout`` → 走拒绝路由

**为什么要 HMAC 签名载荷**：
    - 防篡改：审批人收到的 payload（金额、对方账户）必须和 state 里一致，否则是重放攻击
    - 防越权：payload 里带 required_role，审批人角色不匹配直接拒
    - 签名密钥在服务端 Vault 里，审批链路任何环节都改不了 payload
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import get_settings


@dataclass
class ApprovalPayload:
    """审批载荷。发给审批人的飞书消息 / 邮件里会嵌入 payload + sig。"""

    graph_id: str                # checkpoint 的 thread_id
    state_hash: str              # 当前 state 的 hash，防篡改
    draft_preview: str           # 最多 2000 字，给人看摘要用
    required_role: str           # "preparer" / "approver" / "cfo"
    amount: float                # 涉及金额（用于升级阶梯判断）
    expires_at_ms: int           # 过期时间戳
    metadata: dict[str, Any] = field(default_factory=dict)


def state_hash(state: dict) -> str:
    """对 state 做稳定 hash。排除 messages（太长且不稳定）。"""
    filtered = {k: v for k, v in state.items() if k != "messages"}
    blob = json.dumps(filtered, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build_approval_payload(
    graph_id: str,
    state: dict,
    required_role: str,
    amount: float,
    ttl_sec: int = 24 * 3600,
) -> tuple[ApprovalPayload, str]:
    """构造审批载荷并返回 HMAC 签名。

    Returns:
        ``(payload, hex_sig)``
    """
    payload = ApprovalPayload(
        graph_id=graph_id,
        state_hash=state_hash(state),
        draft_preview=(state.get("draft") or "")[:2000],
        required_role=required_role,
        amount=amount,
        expires_at_ms=int((time.time() + ttl_sec) * 1000),
    )
    sig = _sign(payload)
    return payload, sig


def verify_signature(payload_dict: dict, sig: str) -> bool:
    """Webhook 回调时验签。"""
    try:
        payload = ApprovalPayload(**payload_dict)
    except TypeError:
        return False
    if payload.expires_at_ms < time.time() * 1000:
        return False  # 已过期
    return hmac.compare_digest(_sign(payload), sig)


def _sign(payload: ApprovalPayload) -> str:
    secret = get_settings().hitl_hmac_secret.encode()
    blob = json.dumps(asdict(payload), sort_keys=True, ensure_ascii=False).encode()
    return hmac.new(secret, blob, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# 升级阶梯（金额阈值 → 谁能批）
# ---------------------------------------------------------------------------
def required_role_for_amount(amount: float) -> str:
    """根据金额决定至少需要哪个角色审批。

    阈值配置可从 OPA / config 读，本 demo 写死。
    """
    if amount <= 10_000:
        return "preparer"
    if amount <= 100_000:
        return "approver"
    return "cfo"
