"""痛点 3：严格权限 + 用户态 Token 透传 + 进 LLM 前脱敏 + 注入防御 + 审计。

核心理念（★ 面试必讲）：

    **权限不做在 Agent 层，做在 DB 层的 RLS；Agent 只负责 token 搬运。**
    这样业务权限模型只有一份，Agent 被越狱/注入也没用——DB 那头拒查。

本包分工：
    - :mod:`oauth`           —— OAuth2 PKCE + JWT 验签（FastAPI middleware 骨架）
    - :mod:`token_context`   —— ``ContextVar[user_token]`` 贯穿异步调用栈
    - :mod:`policy_engine`   —— OPA Rego 策略加载骨架（生产接 OPA server）
    - :mod:`column_tagger`   —— 列级 PII 元数据
    - :mod:`redactor`        —— 结果集级脱敏（进 LLM 前）
    - :mod:`audit_log`       —— append-only + HMAC
    - :mod:`injection_guard` —— <tool_result> 隔离 + prompt 注入正则拦截
"""
