# FinAuditAgent

一个面向财务审计场景的 AI Agent 工程项目。

它聚焦的不是“让模型更会聊天”，而是“当任务涉及数字、权限、审批和可追溯性时，怎样把系统做得可控”。项目覆盖了财务 Agent 常见的几类高风险问题：精确计算、Text-to-SQL 安全、权限透传、Human-in-the-Loop 审批、事务回滚、复杂 PDF 文档理解、数据血缘与评测。

![总体架构](docs/images/architecture-overview.drawio.png)

## 项目目标

假设审计员提交这样的问题：

1. “Q1 市场部销售费用率是多少？是否超预算？”
2. “这张报销单是否合规？请给出审计意见。”
3. “研发部 2024 年哪些月份费用环比异常？”

一个可用的系统不能只“回答得像样”，还需要满足下面这些约束：

- 数字不能靠 LLM 直接生成
- SQL 不能随意访问数据库
- 权限必须跟随用户身份全链路透传
- 审批类动作必须支持人工介入
- 外部副作用失败时要能回滚
- 文档结论要能回到原始证据

这个仓库就是围绕这些约束做的工程化原型。

## 核心设计

- **精确计算**：LLM 只生成 Python 代码，实际计算交给沙箱执行；报告中的数字必须附带 evidence pointer。
- **SQL 护栏**：通过 schema linking、语义层、AST 白名单校验和有限重试，降低 Text-to-SQL 误查和危险查询风险。
- **权限与脱敏**：用户 token 通过 `ContextVar` 透传，工具调用前做 scope 校验，结果进入模型前做脱敏。
- **流程编排**：主链路用 LangGraph 状态机建模，审批节点可中断、可恢复。
- **事务一致性**：副作用执行采用 Saga 补偿模式，失败后按逆序回滚，并通过幂等键避免重复执行。
- **复杂文档理解**：先做版面分析，再按表格/章节结构切分，支持带坐标的引证。
- **可信性建设**：提供 evals、审计日志、数据血缘和红队样例，便于持续验证系统质量。

## 仓库结构

```text
FinAuditAgent/
├── fin_audit_agent/          主代码
│   ├── auth/                 权限、脱敏、审计、注入防御
│   ├── graph/                LangGraph 主流程、HITL、Saga
│   ├── lineage/              数据血缘追踪
│   ├── observability/        trace、成本、缓存
│   ├── rag/                  版面分析、切分、检索、引证
│   ├── sandbox/              沙箱执行与数字校验
│   ├── sql_agent/            schema linking、validator、executor
│   ├── tools/                面向 LLM 的工具封装
│   └── cli.py                CLI 入口
├── docs/                     设计说明文档
├── examples/                 独立可运行的 demo
├── evals/                    评测脚本与数据集
├── sandbox_image/            生产沙箱镜像骨架
├── tests/                    单元测试
├── environment.yml
└── pyproject.toml
```

## 重点模块

| 主题 | 代码 | Demo | 文档 |
|---|---|---|---|
| 数字校验与沙箱 | `fin_audit_agent/sandbox/` | `examples/01_sandbox_number_verifier.py` | `docs/01_sandbox_guide.md` |
| Text-to-SQL | `fin_audit_agent/sql_agent/` | `examples/02_sql_schema_linking.py` | `docs/02_text_to_sql_guide.md` |
| 权限透传与脱敏 | `fin_audit_agent/auth/` | `examples/03_rbac_token_passthrough.py` | `docs/03_rbac_token_passthrough.md` |
| LangGraph + HITL + Saga | `fin_audit_agent/graph/` | `examples/04_graph_hitl_demo.py` `examples/06_saga_rollback.py` | `docs/04_graph_hitl_saga.md` |
| 文档理解与 RAG | `fin_audit_agent/rag/` | `examples/05_layout_rag_mini.py` | `docs/05_doc_rag_layout.md` |
| 评测与可观测 | `evals/` `fin_audit_agent/observability/` | - | `docs/06_evals_observability.md` |
| 数据血缘与红队 | `fin_audit_agent/lineage/` | `examples/07_end_to_end_audit.py` | `docs/07_lineage_redteam.md` |

## 快速开始

项目使用 conda 管理环境：

```bash
conda env create -f environment.yml
conda activate fin-audit-agent
cp .env.example .env
```

最少需要配置：

```bash
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

运行测试：

```bash
pytest tests/ -v
```

运行示例：

```bash
python examples/01_sandbox_number_verifier.py
python examples/02_sql_schema_linking.py
python examples/03_rbac_token_passthrough.py
python examples/04_graph_hitl_demo.py
python examples/05_layout_rag_mini.py
python examples/06_saga_rollback.py
python examples/07_end_to_end_audit.py
```

## 推荐阅读顺序

1. `docs/00_architecture.md`：整体架构和主数据流
2. `docs/01_sandbox_guide.md`：为什么不能让 LLM 直接写数字
3. `docs/02_text_to_sql_guide.md`：Text-to-SQL 的护栏设计
4. `docs/03_rbac_token_passthrough.md`：权限、脱敏、审计
5. `docs/04_graph_hitl_saga.md`：状态机、审批中断、补偿事务
6. `docs/05_doc_rag_layout.md`：复杂财务文档的 RAG 管线
7. `docs/06_evals_observability.md`：评测、监控、成本控制
8. `docs/07_lineage_redteam.md`：数据血缘与安全验证

## 当前边界

这是一个工程化原型，重点在架构和关键机制验证，当前仍有一些刻意保留的边界：

- 没有前端界面，主要通过 CLI 和示例脚本演示
- 没有接入真实 ERP、审批、通知系统，外部动作以 mock 为主
- 本地默认使用 RestrictedPython 进行开发态沙箱演示，生产环境应替换为更强隔离方案
- OCR 与多模态能力使用了轻量 demo 路径，真实业务场景需要对接正式模型与数据流

## 适合怎么使用这个仓库

- 把它当成一个高风险 Agent 场景下的工程案例
- 从某个主题切入，例如 Text-to-SQL、HITL 或数据血缘
- 直接运行 `examples/` 里的 demo，观察每个模块的行为
- 用 `tests/` 和 `evals/` 作为后续扩展的基线

## License

MIT
