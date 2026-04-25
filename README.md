<p align="right">
  <a href="README.md">中文</a>
</p>

# FinAuditAgent — 把审计约束落地的 Agent 工程原型

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![LangGraph](https://img.shields.io/badge/orchestration-LangGraph-orange)](https://github.com/langchain-ai/langgraph)

一个面向财务审计场景的 Agent 工程原型。真实业务里有几类约束绕不过去：数字不能靠 LLM 编、SQL 不能随便跑、用户权限要透传到底层、关键操作要等人点确认、失败要能回滚、文档要能引证到页。这个项目把这几类约束逐一落地了。

---

## 架构

![总体架构](docs/images/architecture-overview.drawio.png)

一次请求经过 9 个节点的 LangGraph 状态机：

```
User Input → Intake → Planner → DataFetch ──→ Analyze ──→ Drafter
                         │           │            │
                         └─ DocRAG ──┘            │
                                                  ▼
                              HumanReview ←────────
                                  │
                              Execute → Notify → 最终结果
```

- **Planner** 根据问题拆分步骤（查哪些数据、读哪些文档、算什么指标）
- **DataFetch** 通过 Text-to-SQL 查数据库，三层护栏保证只读
- **DocRAG** 从财务文档中检索相关内容，返回带位置的片段
- **Analyze** 在沙箱里执行 Python，计算指标，数字全部可追溯
- **Drafter** 生成带引证的审计意见草案
- **HumanReview** 挂起，等人审批（LangGraph interrupt）
- **Execute** 执行副作用，失败按 Saga 模式补偿回滚
- **Notify** 返回最终结果

审批被拒绝时路由到 Clarify 节点，根据反馈修改后重新走 Drafter → HumanReview 循环。

---

## 六个约束怎么落地的

### 1. 数字不能靠 LLM 编

**问题**：LLM 生成 token 时会出现幻觉。让它直接写数字（"Q1 营收同比增长 12.4%"），出错的概率很高。

**方案**：LLM 只生成 Python 代码，代码放进沙箱执行，结果里的大写变量自动抽取作为"cell"供 Drafter 引用。

```python
# LLM 生成的代码（不是 LLM 编的数字）
REVENUE_Q1 = df[df['quarter'] == 'Q1']['revenue'].sum()
REVENUE_Q2 = df[df['quarter'] == 'Q2']['revenue'].sum()
GROWTH = round((REVENUE_Q2 - REVENUE_Q1) / REVENUE_Q1 * 100, 1)
# GROWTH = 12.4  ← 这个数字来自数据，不是编的
```

`number_verifier` 会检查报告里的每个数字是否都能追溯到 sandbox 里的一个 cell。

**代码位置**：`fin_audit_agent/sandbox/runner.py`、`number_verifier.py`

### 2. SQL 不能随便跑

**问题**：LLM 生成的 SQL 可能包含 DROP、INSERT，或者直接 `SELECT *` 扫全表。用正则校验 SQL 永远绕得过。

**方案**：三重防线，全部通过 sqlglot AST 解析实现（不是正则）：

- **语句类型白名单**：只允许 SELECT，其他一律拦截
- **自动 LIMIT 注入**：没显式 LIMIT 的自动加 `LIMIT 10000`
- **高危函数黑名单**：禁 `pg_read_file`、`COPY`、`lo_export` 等可触达 OS 的函数

```python
# validator.py 的核心逻辑
if node.key not in {"select", "set"}:
    return ValidateResult(ok=False, reason=f"DML/DDL not allowed: {node.key}")
if "limit" not in {c.key for c in node.find_all(sqlglot.exp.Limit)}:
    sql = f"{sql} LIMIT 10000"  # 自动注入
```

配上 schema linking（先用 jieba + BM25 缩小候选表范围）和 semantic layer（统一业务指标口径），形成一条完整的 Text-to-SQL 安全链路。

**代码位置**：`fin_audit_agent/sql_agent/validator.py`、`schema_linker.py`、`semantic_layer.py`

### 3. 用户权限要透传到底层

**问题**：大多数 LLM 应用把用户身份丢在 API 网关层，底层工具调用用的全是 service account。审计场景里，"谁查的"和"查到了什么"一样重要。

**方案**：用 `ContextVar`（PEP 567）透传用户 token，不经过 LLM 上下文：

```python
# 入口：用户 token 注入 ContextVar
token = user_token_var.set(UserToken(sub="u123", role="auditor",
                                      scopes=["read:finance"]))
try:
    result = await graph.ainvoke({"question": "Q1 销售费用异常"})
finally:
    user_token_var.reset(token)

# 下游 SQL 执行器自动读取：
tok = user_token_var.get()
sql = f"SET app.current_user_id = '{tok.sub}'; {user_sql}"
# PostgreSQL RLS 策略自动生效
```

**为什么不用全局变量或 threading.local**：LangGraph 的节点可能在 asyncio task 或 thread pool 里跑，只有 ContextVar 能保证不同并发请求之间自动隔离。

**代码位置**：`fin_audit_agent/auth/token_context.py`、`redactor.py`、`audit_log.py`

### 4. 关键操作要等人点确认

**问题**：AI 可以辅助分析，但最终调账、打款、发通知这些操作需要有人确认。

**方案**：LangGraph 的 `interrupt_before=["execute"]` + checkpoint 实现：

```python
graph = builder.build_graph(interrupt_before_execute=True)
config = {"configurable": {"thread_id": "audit-001"}}

# 第一次 invoke：跑到 HumanReview，吐出审批载荷（含 HMAC 签名），然后停住
result = graph.invoke({"question": "Q1 异常交易分析"}, config=config)

# 审批人确认后，注入审批结果，继续执行
graph.update_state(config, {"approval_status": "approved"})
final = graph.invoke(None, config=config)
```

审批载荷用 HMAC 签名，防止前端篡改。拒绝时走 Clarify 节点，把审批意见喂回去重新生成草案。

**代码位置**：`fin_audit_agent/graph/builder.py`、`hitl.py`、`checkpoint.py`

### 5. 失败要能回滚

**问题**：Execute 节点可能做多步有副作用的操作（记账 + 更新工单 + 发通知）。如果第三步失败，前两步不能留半成品。

**方案**：Saga 模式——每步注册 `(执行函数, 补偿函数, 幂等键)`，顺序执行，失败时逆序调补偿：

```python
saga = Saga(steps=[
    Step(name="book_entry",    do=book, compensate=reverse_book),
    Step(name="update_ticket", do=update, compensate=rollback_ticket),
    Step(name="send_notify",   do=notify, compensate=recall_notification),
])
saga.execute()
# 如果 send_notify 失败，先调 rollback_ticket，再调 reverse_book
```

不是 2PC，不需要分布式锁。每步本地事务立即 commit，compensate 可能登记反向操作（比如给已打款的做冲正）而非物理删除。

**代码位置**：`fin_audit_agent/graph/saga.py`

### 6. 财务文档要能引证到页

**问题**：财务报告里经常有 "根据 XX 准则第 X 条..."——引用必须能追溯到原文档的具体位置。

**方案**：先做版面分析识别段落、表格、页眉页脚，再按章节结构切分（不按固定字数），检索时返回带页码和 bbox 的片段，Drafter 把这些引用写进审计意见。

```python
# citation.py 输出的片段示例
{
    "text": "预期信用损失应按相当于整个存续期内预期信用损失的金额计量",
    "source": "企业会计准则第22号.pdf",
    "page": 8,
    "section": "第三章 预期信用损失的计量",
    "bbox": [120, 340, 480, 370]  # 原始 PDF 坐标
}
```

**代码位置**：`fin_audit_agent/rag/layout.py`、`semantic_chunker.py`、`citation.py`

---

## 跑起来

```bash
conda env create -f environment.yml
conda activate fin-audit-agent
cp .env.example .env  # 最少填 OPENAI_API_KEY
```

最少配置就能跑（SQL 用 SQLite，沙箱用 RestrictedPython）：

```bash
pytest tests/ -v                          # 5 个测试：sandbox 隔离、SQL 只读、Saga 回滚、RBAC 透传、graph resume

python examples/07_end_to_end_audit.py    # 端到端：从提问到带引证的审计意见
```

六个独立 demo 分别演示六个约束模块：
```bash
python examples/01_sandbox_number_verifier.py   # 数字校验
python examples/02_sql_schema_linking.py        # SQL 护栏
python examples/03_rbac_token_passthrough.py    # 权限透传
python examples/04_graph_hitl_demo.py           # 人工审批
python examples/05_layout_rag_mini.py           # 文档引证
python examples/06_saga_rollback.py             # 补偿回滚
```

推荐阅读顺序对应 `docs/00_architecture.md` → `01` ~ `05`。

---

## 边界与已知限制

- **RestrictedPython 不是生产级沙箱**。它是编译期 AST 改写，拦截了 `__import__`、`open`、`exec` 等危险原语，但无法防止死循环/OOM/C 扩展内存越界。本地 demo 用的是这条路，生产需要换 Docker + gVisor / nsjail + seccomp 或 e2b 这种云沙箱。

- **没有前端**，主要通过 CLI 和 demo 演示。Graph 的 HITL 审批节点预留了 RESTful 接口（HMAC 签名），但没做 UI。

- **外部系统接口目前以 mock 为主**，ERP / 工单 / 通知的集成是骨架代码（`@route_after_approval` 的分支逻辑在，但下游是 stub）。

- **OCR 和多模态部分是轻量原型**。完整的 PaddleOCR + LayoutLMv3 路径在注释里说明了怎么接入，但当前 demo 用纯文本 PDF 跑。

---

## License

MIT
