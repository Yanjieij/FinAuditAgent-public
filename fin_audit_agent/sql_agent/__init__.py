"""痛点 2：Text-to-SQL + Schema Linking + 容错重试。

完整流水线::

    用户问题
       │
       ▼
    [schema_linker]  ──► 从全库 schema 里召回最相关的 K 张表 + 列
       │
       ▼
    [semantic_layer] ──► 把业务指标 measures/dimensions 塞进 prompt
       │
       ▼
    [sql_gen]        ──► LLM 生成 SQL
       │
       ▼
    [validator]      ──► sqlglot AST 解析 + allowlist + LIMIT 注入
       │  ✗ fail
       │  └──► [retry_loop] ──► 反馈错误，重试（≤3 次）
       │                  失败 → 降级 Clarify
       ▼
    [executor]       ──► 只读连接执行 + timeout
       │
       ▼
    [masker]         ──► 结果集列级脱敏（进 LLM 前）
       │
       ▼
    返回 DataFrame（pandas）

每个模块都独立可测，``tests/test_sql_readonly.py`` 覆盖 allowlist 攻击面。
"""
