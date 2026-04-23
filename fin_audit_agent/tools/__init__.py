"""LangChain ``@tool`` 适配器：把内部能力暴露给 LLM 调用。

**设计要点**：
    - 每个 tool 入口调用 ``auth.token_context.require_scope(...)`` 做权限守卫
    - 所有工具输出经过 ``auth.injection_guard.wrap_untrusted`` 包装后塞 prompt
    - 工具返回结构化 JSON（LangChain 要求），而不是自由文本

工具清单：
    - :func:`sql_tool.sql_query_tool`         —— 用 Text-to-SQL 查数
    - :func:`sandbox_tool.run_python`         —— 在沙箱跑计算代码
    - :func:`rag_tool.search_docs`            —— 查文档
    - :func:`notify_tool.send_feishu`         —— 发飞书（需审批）
"""
