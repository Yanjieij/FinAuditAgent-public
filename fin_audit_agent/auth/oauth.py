"""OAuth2 PKCE + OIDC（骨架，实现示意）。

**生产接入要点**：
    - 和公司 SSO（如 OIDC of Ant 内部、Okta、Azure AD）对接
    - 用 PKCE flow（RFC 7636）避免 code interception 攻击
    - JWT 用 ``python-jose`` 按 JWKS 公钥验签
    - FastAPI 依赖 ``Depends(verify_jwt)`` 挂到所有路由

本文件只给接口骨架与关键函数示例，真实生产时把 TODO 填上即可。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from .token_context import UserToken


@dataclass
class AuthConfig:
    """OAuth2 / OIDC 配置。生产从 Vault 读。"""

    issuer: str = "https://sso.example.com"
    audience: str = "fin-audit-agent"
    jwks_url: str = "https://sso.example.com/.well-known/jwks.json"


def verify_jwt(raw_jwt: str, cfg: AuthConfig | None = None) -> UserToken:
    """验签 JWT → UserToken。

    **骨架实现**：用户已说"代码骨架轻"，这里给出可运行的骨架：
        - 如果 JWT 是 ``dev:<sub>:<role>:<scope1>,<scope2>`` 这样的 mock 格式，
          直接解（方便 examples / tests 跑）
        - 真正的 JWT 会通过 ``python-jose + JWKS`` 验签，这段加注释 TODO

    Args:
        raw_jwt: 原始 JWT / Bearer token
        cfg:     配置

    Returns:
        已验证的 :class:`UserToken`

    Raises:
        PermissionError: 签名错 / 过期 / audience 不匹配
    """
    # ---- dev mock 路径，给 examples 用 ----
    if raw_jwt.startswith("dev:"):
        parts = raw_jwt.split(":")
        if len(parts) < 3:
            raise PermissionError("dev token 格式错误，应为 dev:<sub>:<role>[:<scope1,scope2>]")
        sub, role = parts[1], parts[2]
        scopes = tuple(parts[3].split(",")) if len(parts) > 3 and parts[3] else ()
        return UserToken(
            sub=sub,
            role=role,
            scopes=scopes,
            raw_jwt=raw_jwt,
            token_hash_prefix=_hash_prefix(raw_jwt),
        )

    # ---- 生产路径（TODO）----
    # from jose import jwt as jose_jwt
    # jwks = _load_jwks(cfg.jwks_url)
    # claims = jose_jwt.decode(raw_jwt, jwks, algorithms=["RS256"],
    #                          audience=cfg.audience, issuer=cfg.issuer)
    # return UserToken(sub=claims["sub"], role=claims.get("role", "user"),
    #                  scopes=tuple(claims.get("scope", "").split()), ...)
    raise NotImplementedError(
        "生产 JWT 验签未实现。用 dev:sub:role:scope 格式的 mock token 可以 bypass 到 dev 路径。"
    )


def _hash_prefix(raw_jwt: str) -> str:
    """只留 12 位 hash 给审计日志，不保留原 JWT（避免日志泄漏）。"""
    return hashlib.sha256(raw_jwt.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# FastAPI 集成示例（不强依赖 fastapi，注释里给模板）
# ---------------------------------------------------------------------------
def fastapi_dependency_example():
    """FastAPI 依赖示例。生产里直接拷贝修改。

    ::

        from fastapi import Header, HTTPException, Depends
        from fin_audit_agent.auth.oauth import verify_jwt
        from fin_audit_agent.auth.token_context import user_token_var

        async def auth_dep(authorization: str = Header(...)):
            if not authorization.startswith("Bearer "):
                raise HTTPException(401, "missing bearer")
            try:
                tok = verify_jwt(authorization[7:])
            except PermissionError as e:
                raise HTTPException(401, str(e))
            # 入栈：本请求后续所有 await 都能读到
            user_token_var.set(tok)
            return tok

        @app.post("/ask", dependencies=[Depends(auth_dep)])
        async def ask(q: str): ...
    """
