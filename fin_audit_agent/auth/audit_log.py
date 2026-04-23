"""Append-only 审计日志（带 HMAC 签名）。

**为什么要 HMAC 链式签名**（面试讲点）：

    金融审计要求日志 **不可抵赖、不可篡改**。仅仅 append-only 不够——有 root
    权限的运维还是可以批量改。**HMAC 链式签名**（每条的 signature 依赖上一条的
    signature）让任何单条篡改都会让后面所有条签名都失效，审计回放时能一眼发现。

    可类比区块链的 hash chain，但运维成本低得多（不需要共识）。

**推荐存储**：
    - 开发：SQLite（本文件默认）
    - 生产：append-only 的 Kafka 流 + S3 WORM 归档 + 每日签名锚定到公证服务

用法::

    audit = AuditLog()
    audit.append(
        user=user_token_var.get(),
        action="sql.execute",
        input_hash=hashlib.sha256(sql.encode()).hexdigest()[:16],
        output_hash=hashlib.sha256(str(df).encode()).hexdigest()[:16],
        metadata={"rows": len(df)},
    )
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from .token_context import UserToken


@dataclass
class AuditRecord:
    """一条审计记录。"""

    ts_ms: int                     # 毫秒时间戳
    user_sub: str                  # 用户 ID
    user_role: str                 # 用户角色
    token_hash_prefix: str         # JWT hash 前 12 位（不保留原值）
    tenant: str                    # 租户
    action: str                    # "sql.execute" / "sandbox.run" / "doc.fetch" / ...
    input_hash: str                # 输入 hash
    output_hash: str               # 输出 hash
    metadata: dict[str, Any] = field(default_factory=dict)
    prev_sig: str = ""             # 上一条的签名（链式）
    sig: str = ""                  # 本条签名


class AuditLog:
    """SQLite 实现的 append-only 审计日志。"""

    def __init__(self, db_path: str | Path = ".fin_audit_log.db", secret: str | None = None):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit
        self._conn.row_factory = sqlite3.Row
        self._secret = (secret or get_settings().audit_hmac_secret).encode()
        self._ensure()

    def _ensure(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                user_sub TEXT NOT NULL,
                user_role TEXT NOT NULL,
                token_hash_prefix TEXT,
                tenant TEXT,
                action TEXT NOT NULL,
                input_hash TEXT,
                output_hash TEXT,
                metadata TEXT,
                prev_sig TEXT,
                sig TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_sub);
            """
        )

    # ---------- 写入 ----------
    def append(
        self,
        user: UserToken,
        action: str,
        input_hash: str = "",
        output_hash: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> AuditRecord:
        last_sig = self._last_sig()
        rec = AuditRecord(
            ts_ms=int(time.time() * 1000),
            user_sub=user.sub,
            user_role=user.role,
            token_hash_prefix=user.token_hash_prefix,
            tenant=user.tenant,
            action=action,
            input_hash=input_hash,
            output_hash=output_hash,
            metadata=metadata or {},
            prev_sig=last_sig,
        )
        rec.sig = self._sign(rec)
        self._insert(rec)
        return rec

    # ---------- 验证 ----------
    def verify_chain(self) -> tuple[bool, str]:
        """遍历所有记录，验证 HMAC 链。返回 (ok, reason)。"""
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY id ASC"
        ).fetchall()
        last_sig = ""
        for r in rows:
            rec = AuditRecord(
                ts_ms=r["ts_ms"],
                user_sub=r["user_sub"],
                user_role=r["user_role"],
                token_hash_prefix=r["token_hash_prefix"] or "",
                tenant=r["tenant"] or "default",
                action=r["action"],
                input_hash=r["input_hash"] or "",
                output_hash=r["output_hash"] or "",
                metadata=json.loads(r["metadata"] or "{}"),
                prev_sig=r["prev_sig"] or "",
            )
            if rec.prev_sig != last_sig:
                return False, f"chain_break at id={r['id']}"
            expected = self._sign(rec)
            if expected != r["sig"]:
                return False, f"sig_mismatch at id={r['id']}"
            last_sig = r["sig"]
        return True, "ok"

    # ---------- 内部 ----------
    def _last_sig(self) -> str:
        row = self._conn.execute(
            "SELECT sig FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["sig"] if row else ""

    def _sign(self, rec: AuditRecord) -> str:
        payload = json.dumps(
            {
                "ts_ms": rec.ts_ms,
                "user_sub": rec.user_sub,
                "user_role": rec.user_role,
                "token_hash_prefix": rec.token_hash_prefix,
                "tenant": rec.tenant,
                "action": rec.action,
                "input_hash": rec.input_hash,
                "output_hash": rec.output_hash,
                "metadata": rec.metadata,
                "prev_sig": rec.prev_sig,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def _insert(self, rec: AuditRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO audit_log (ts_ms, user_sub, user_role, token_hash_prefix,
                                   tenant, action, input_hash, output_hash,
                                   metadata, prev_sig, sig)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec.ts_ms,
                rec.user_sub,
                rec.user_role,
                rec.token_hash_prefix,
                rec.tenant,
                rec.action,
                rec.input_hash,
                rec.output_hash,
                json.dumps(rec.metadata, ensure_ascii=False),
                rec.prev_sig,
                rec.sig,
            ),
        )

    def close(self) -> None:
        self._conn.close()


def hash_text(s: str, prefix: int = 16) -> str:
    """一个公用的内容 hash 工具，给 audit 调用者用。"""
    return hashlib.sha256(s.encode()).hexdigest()[:prefix]
