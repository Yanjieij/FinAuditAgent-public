"""飞书/企微通知工具（mock）。

**重要**（面试讲点）：
    - 通知工具涉及"副作用"，**必须在 HITL 审批后才能调**
    - Agent 调用时检查 ``state['approval_status'] == 'approved'``（实际由 graph 的
      ``interrupt_before=["execute"]`` 结构保证）
    - 支持失败补偿：``compensate_feishu`` 发一条撤销通知
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..auth.audit_log import AuditLog, hash_text
from ..auth.token_context import current_user, require_scope


@dataclass
class NotifyReceipt:
    ok: bool
    message_id: str  # 真实平台返回的消息 ID
    sent_at_ms: int


_MOCK_INBOX: list[dict] = []  # 进程内 mock


def send_feishu(text: str, channel: str = "finance-team") -> NotifyReceipt:
    """发飞书消息（mock）。

    生产接入：
        import httpx
        httpx.post(WEBHOOK_URL, json={"msg_type": "text", "content": {"text": text}})

    **权限**：需 ``notify:external`` scope；本函数应该只在 Execute 节点被调。
    """
    require_scope("notify:external")
    user = current_user()

    receipt = NotifyReceipt(
        ok=True,
        message_id=f"mock-{int(time.time() * 1000)}",
        sent_at_ms=int(time.time() * 1000),
    )
    _MOCK_INBOX.append({
        "channel": channel,
        "text": text,
        "user": user.sub,
        "message_id": receipt.message_id,
    })

    try:
        AuditLog().append(
            user=user,
            action="notify.feishu",
            input_hash=hash_text(text),
            output_hash=receipt.message_id,
            metadata={"channel": channel, "ok": receipt.ok},
        )
    except Exception:
        pass
    return receipt


def compensate_feishu(message_id: str) -> None:
    """撤销飞书消息（mock）——Saga 补偿用。"""
    _MOCK_INBOX.append({"compensate": message_id})


def mock_inbox() -> list[dict]:
    """给 tests / examples 查 mock 收件箱的工具函数。"""
    return list(_MOCK_INBOX)
