"""FinAuditAgent —— 企业级智能财务审计与分析 Agent.

本包的组织原则：**按痛点分包**，而不是按技术栈分包。每个子包对应 docs/ 下的一篇技术文档：

- ``sandbox/``     —— 痛点 1：零幻觉 + 精准计算（Code Interpreter 沙箱）
- ``sql_agent/``   —— 痛点 2：Text-to-SQL + Schema Linking + 容错重试
- ``auth/``        —— 痛点 3：RBAC + Token 透传 + 脱敏 + 审计
- ``graph/``       —— 痛点 4：LangGraph FSM + HITL + Saga 补偿
- ``rag/``         —— 痛点 5：复杂财务文档 RAG（版面分析）
- ``tools/``       —— LangChain @tool 适配器，把上述能力暴露给 LLM
- ``observability/`` —— OpenTelemetry + Langfuse + 成本预算
- ``lineage/``     —— 数据血缘（每个数字可溯源）
"""

__version__ = "0.1.0"
