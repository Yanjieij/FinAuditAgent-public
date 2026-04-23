"""可观测性 + 成本控制 + 语义缓存。

- :mod:`otel_setup`       —— OpenTelemetry span 接入
- :mod:`langfuse_setup`   —— Langfuse trace/token/cost（自托管）
- :mod:`cost_budget`      —— 单请求 token 预算 + 模型路由
- :mod:`semantic_cache`   —— Redis + embedding 相似度的语义缓存（骨架）
"""
