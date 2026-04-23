# FinAuditAgent

一个面向财务审计场景的 Agent 工程原型。

项目重点不在“多智能”，而在几类常见约束怎么落地：数字校验、Text-to-SQL 护栏、权限透传、人工审批、补偿事务、文档引证和数据血缘。

![总体架构](docs/images/architecture-overview.drawio.png)

## 先看怎么用

### 1. 初始化环境

```bash
conda env create -f environment.yml
conda activate fin-audit-agent
cp .env.example .env
```

至少配置：

```bash
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

### 2. 跑测试

```bash
pytest tests/ -v
```

### 3. 跑 demo

```bash
python examples/01_sandbox_number_verifier.py
python examples/02_sql_schema_linking.py
python examples/03_rbac_token_passthrough.py
python examples/04_graph_hitl_demo.py
python examples/05_layout_rag_mini.py
python examples/06_saga_rollback.py
python examples/07_end_to_end_audit.py
```

如果只想先看完整链路，直接跑：

```bash
python examples/07_end_to_end_audit.py
```

## 主流程

一次请求的大致流转如下：

1. 用户输入问题或上传报销单
2. `Planner` 拆分步骤
3. `DataFetch` 查数据库
4. `DocRAG` 读文档并返回带位置的片段
5. `Analyze` 在沙箱里执行 Python 计算
6. `Drafter` 生成带引证的审计意见
7. `HumanReview` 中断，等待审批
8. `Execute` 执行副作用，并在失败时按 Saga 回滚
9. `Notify` 返回最终结果

对应代码主要在 `fin_audit_agent/graph/`。

## 主要功能

### 1. 数字不直接让 LLM 写

- LLM 只生成 Python 代码
- 代码放进沙箱执行
- 报告里的数字需要带 evidence pointer
- `number_verifier` 会检查有没有漏掉未引证数字

相关代码：

- `fin_audit_agent/sandbox/runner.py`
- `fin_audit_agent/sandbox/number_verifier.py`
- `examples/01_sandbox_number_verifier.py`

### 2. Text-to-SQL 加护栏

- schema linking 先缩小候选表范围
- semantic layer 统一业务指标口径
- SQL 生成后做白名单校验
- 执行失败时有限次重试

相关代码：

- `fin_audit_agent/sql_agent/schema_linker.py`
- `fin_audit_agent/sql_agent/semantic_layer.py`
- `fin_audit_agent/sql_agent/validator.py`
- `fin_audit_agent/sql_agent/retry_loop.py`
- `examples/02_sql_schema_linking.py`

### 3. 权限、脱敏、审计链

- 用户 token 通过 `ContextVar` 透传
- 工具调用前检查 scope
- 结果进入模型前做脱敏
- 关键操作写入链式审计日志

相关代码：

- `fin_audit_agent/auth/token_context.py`
- `fin_audit_agent/auth/redactor.py`
- `fin_audit_agent/auth/audit_log.py`
- `examples/03_rbac_token_passthrough.py`

### 4. LangGraph + HITL + Saga

- 主流程是固定状态机
- 审批节点通过 interrupt 停图
- 批准后从 checkpoint 恢复
- 副作用执行使用 Saga 补偿

相关代码：

- `fin_audit_agent/graph/builder.py`
- `fin_audit_agent/graph/hitl.py`
- `fin_audit_agent/graph/checkpoint.py`
- `fin_audit_agent/graph/saga.py`
- `examples/04_graph_hitl_demo.py`
- `examples/06_saga_rollback.py`

### 5. 财务文档 RAG

- 先做版面分析
- 再按章节和表格结构切分
- 检索后返回带页码和 bbox 的引证

相关代码：

- `fin_audit_agent/rag/layout.py`
- `fin_audit_agent/rag/semantic_chunker.py`
- `fin_audit_agent/rag/hybrid_retriever.py`
- `fin_audit_agent/rag/citation.py`
- `examples/05_layout_rag_mini.py`

### 6. 数据血缘和评测

- 结果可以追溯到 SQL、文档片段和沙箱执行
- 提供 eval dataset 和 red-team 样例

相关代码：

- `fin_audit_agent/lineage/tracker.py`
- `evals/run_eval.py`
- `evals/redteam_suite.py`
- `examples/07_end_to_end_audit.py`

## 仓库结构

```text
fin_audit_agent/
  auth/             权限、脱敏、审计、注入防御
  graph/            主流程状态机、HITL、Saga、checkpoint
  lineage/          数据血缘
  observability/    trace、成本预算、语义缓存
  rag/              文档解析、切分、检索、引证
  sandbox/          沙箱执行和数字校验
  sql_agent/        Text-to-SQL 相关模块
  tools/            提供给模型调用的工具
docs/               设计说明
examples/           可独立运行的 demo
evals/              评测脚本和数据集
tests/              单元测试
```

## 推荐阅读顺序

1. `examples/07_end_to_end_audit.py`
2. `docs/00_architecture.md`
3. `docs/01_sandbox_guide.md`
4. `docs/02_text_to_sql_guide.md`
5. `docs/04_graph_hitl_saga.md`
6. `docs/03_rbac_token_passthrough.md`
7. `docs/05_doc_rag_layout.md`

## 当前边界

- 没有前端，主要通过 CLI 和 demo 演示
- 外部系统接口目前以 mock 为主
- 本地沙箱使用 RestrictedPython，生产环境应替换为更强隔离方案
- OCR 和多模态部分是轻量原型实现

## License

MIT
