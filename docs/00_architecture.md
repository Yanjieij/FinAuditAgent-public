# 00 · 项目整体架构

这篇是所有文档的入口。读完之后，你应该能做到两件事：

第一，在脑子里大致画出这个 Agent 是怎么工作的——用户从哪里进来，数据怎么流动，最后怎么产生一份报告。

第二，看到代码目录里的某个文件，能大概猜出它是干什么的、属于哪个环节。

如果读完还有模糊的地方，别担心，后面每一篇文档都会把某一块拎出来单独详细讲。

## 先从一个真实场景开始

想象你是公司的审计员，今天收到一张张三的报销单，金额 2720 元。你要判断：这张单子合不合规？有没有超预算？

如果手工做，流程大概是：

1. 拿过报销单，看上面写的申请人、部门、金额、日期、事由
2. 到财务系统里查张三所在部门（市场部）本月的差旅预算
3. 拿出计算器，算一下报销金额 vs 预算的差额和超支率
4. 打开 Word，写一份审计意见
5. 交给你的上级审批
6. 上级同意后，在财务系统里点"通过"，并通知申请人

这个 Agent 要做的就是把 1-6 自动化。但注意，第 5 步"上级审批"**不能自动化**——涉及金额的决策必须真人点头。第 6 步涉及真实的副作用（记账、发通知），失败了要能回滚。

我们整个项目就是按这个流程设计的。

## 整体架构图

![总体架构](images/architecture-overview.drawio.png)

下面是同一张图的 ASCII 版本，可以对照着看：

```
用户
 │
 │  用户是谁？通过 OAuth2 + JWT 验证。验证通过后拿到一个 UserToken，
 │  里面记录了用户的 ID、角色、权限范围。
 ▼
┌────────────────────────────────────────────────────────────┐
│                   API 网关 + 身份校验                       │
│               (FastAPI + Authlib 做 JWT 验签)               │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           │  UserToken 被放进 Python 的 ContextVar，
                           │  后面整条处理链路都能读到它。这是权限透传的基础。
                           ▼
┌────────────────────────────────────────────────────────────┐
│            主流程（LangGraph 编排的有限状态机）              │
│                                                            │
│   Intake ── Clarify ── Planner                             │
│                           │                                │
│         ┌─────────────────┼────────────────────┐           │
│         ▼                 ▼                    ▼           │
│     DataFetch          DocRAG                Analyze       │
│     （查 SQL）          （读 PDF）           （沙箱算数）   │
│         │                 │                    │           │
│         └─────────────────┼────────────────────┘           │
│                           ▼                                │
│                        Drafter                             │
│                    （起草报告）                             │
│                           │                                │
│                           ▼                                │
│              ⏸ HumanReview（图停在这里，等审批）            │
│                           │                                │
│                           ▼                                │
│                        Execute                             │
│                （Saga 模式执行副作用）                       │
│                           │                                │
│                           ▼                                │
│                        Notify                              │
│                                                            │
│   状态会被 PostgresSaver 落盘到数据库，整个图随时可以从中   │
│   断点恢复——Worker 挂了、审批拖了三天，都能继续跑下去。    │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────┐
│               横切关注点（贯穿所有环节）                     │
│                                                            │
│  · 审计日志：每次工具调用都记录到 append-only 的表里，       │
│    用 HMAC 做链式签名，任何一条被篡改都能立刻察觉            │
│  · Trace：通过 Langfuse 或 OpenTelemetry 把所有步骤串起来    │
│  · 成本预算：每个请求有 token 和金额上限，超了就降级         │
│  · 数据血缘：报告里每个数字都能追溯到 SQL / 文档 / 沙箱      │
└────────────────────────────────────────────────────────────┘
```

这张图可能看着密，但你不用一次消化完。我们接下来逐块讲。

## 主流程的九个节点

项目的核心是一个"状态机"（FSM, Finite State Machine），用 LangGraph 这个库来实现。状态机的意思是：**整个流程被拆成一串节点，每个节点只做一件事，节点之间的跳转规则是提前写死的**。

对比一下"自由 Agent"（也就是最简单的 ReAct 模式）：ReAct 里，LLM 每一步都自己决定"我下一步调哪个工具"，流程完全由 LLM 控制。这种模式灵活，但不可控——它可能循环 10 次才收敛，可能烧光成本预算，关键节点（比如审批）也没地方插入。

我们的做法是折中：顶层流程钉死，只在"分析"这个子节点里允许 LLM 自由调工具（但设了最多 5 步的上限）。下面逐个说这九个节点：

**Intake（接收）**：拿到用户的问题，初始化状态。这个节点不调 LLM，只是把一些字段设成默认值，保持确定性。

**Clarify（澄清）**：判断问题是不是够清楚。如果用户问得太模糊（比如"帮我看下那笔账"），就反问一句。有时候是下游的 SQL 查询失败了、降级回来触发澄清。

**Planner（规划）**：调 LLM 把用户问题拆成 2-5 个可执行的步骤。每一步会被标记成 `data_fetch`、`doc_rag` 或 `analyze` 之一，告诉后面的节点该做什么。

**DataFetch（查数据库）**：执行所有 `data_fetch` 类型的步骤。这里面套了一个子流程：Schema Linking → SQL 生成 → 校验 → dry-run → 真实执行 → 脱敏。详见 `docs/02`。

**DocRAG（读文档）**：执行所有 `doc_rag` 步骤。流程是：版面分析 → 结构化抽取 → 混合检索 → 重排 → 带引证返回。详见 `docs/05`。

**Analyze（分析）**：在沙箱里跑 Python 代码做计算。这是唯一允许 LLM 自由调用工具的地方，但迭代次数有上限。详见 `docs/01`。

**Drafter（起草）**：综合前面三个节点的产物，起草一份报告。关键约束是：**报告里的每个数字都必须带引证标签**，指向它的来源（某个沙箱执行的某个变量）。如果没带，节点会自动打回重写。

**HumanReview（人工审批）**：这里是整个流程的"刹车"。图会在这个节点之前停下，把当前状态持久化到数据库。我们给审批人发一条飞书消息，消息里带一个 HMAC 签名的载荷。审批人点同意 → 我们验签 → 更新状态 → 从这个节点继续往下走。详见 `docs/04`。

**Execute（执行）**：真正产生副作用的地方——在财务系统里记一笔账、发飞书通知、更新工单。这里用 **Saga 模式**：每一步都有对应的"补偿函数"，任何一步失败就按反向顺序回滚。而且每一步都有幂等键，即使从中间崩溃重启也不会重复执行。详见 `docs/04`。

**Notify（通知）**：把最终报告返回给用户。

## 为什么要这么多节点

你可能会问："为什么不能一步到位，让 LLM 一次性搞定？"

答案是：每加一个节点，都是在加一处"观察点"。观察点越多，出问题时越容易定位。

比如跑完一个请求，你发现报告里的数字错了。如果整个流程是个黑盒，你只能盯着最后的输出发呆。而在我们的架构里，你可以：

- 看 Intake 节点的输入，确认用户问题没问题
- 看 Planner 节点的输出，确认拆分合理
- 看 DataFetch 节点的 SQL 查询结果，确认从数据库拿到的值是对的
- 看 Analyze 节点的沙箱执行记录，确认计算过程是对的
- 看 Drafter 节点的原始草稿，确认数字是什么时候写错的

这种"沿途留痕"的工程方式，是复杂 LLM 系统能不能上生产的分水岭。

## 一个具体请求的流转

下面这张时序图画的就是本节要讲的完整链路，你可以先扫一眼再读文字：

![端到端时序](images/end-to-end-sequence.drawio.png)

还是那个报销单审核的例子。完整的数据流是这样的：

1. **用户在前端提问**："这张报销单是否超预算？"，附带 PDF 文件。
2. **FastAPI 网关**校验 JWT，把用户身份放进 `ContextVar`。
3. **Intake** 节点接收问题，设置 `iterations=0`、`verdict=""` 等初始值。
4. **Clarify** 节点检查 Schema Linking 的匹配情况（用户提了"报销单"、"预算"这两个关键词，我们的 schema 索引里都能找到对应的表），判断不需要澄清。
5. **Planner** 调 LLM，返回一个 plan：`["doc_rag: 抽报销单 KV", "data_fetch: 查市场部差旅预算", "analyze: 算超支率"]`。
6. **DocRAG** 读 PDF，抽出 KV：申请人=张三，金额=2720，类别=差旅。同时产出一个 `RAGChunkRef`，带有原 PDF 的页码和 bbox（就是那块区域的坐标）。
7. **DataFetch** 生成 SQL `SELECT travel_budget FROM dim_department WHERE name='市场部'`，通过校验后执行，拿到结果 2000。
8. **Analyze** 在沙箱里跑：

   ```python
   BUDGET = 2000
   REIMBURSEMENT = 2720
   OVERRUN_AMOUNT = REIMBURSEMENT - BUDGET     # = 720
   OVERRUN_RATE = OVERRUN_AMOUNT / BUDGET * 100 # = 36
   ```

   执行完返回一个 `exec_id`（比如 `a1b2c3d4e5f6`），以及所有大写变量的值。

9. **Drafter** 调 LLM 写报告。报告里每个数字都带标签：

   ```
   报销金额 2720.00 元 [citation:REIMB-001#page=1#bbox=...]
   超预算 720 元 [[exec_id=a1b2c3d4e5f6#cell=OVERRUN_AMOUNT]]
   超支率 36% [[exec_id=a1b2c3d4e5f6#cell=OVERRUN_RATE]]
   ```

   节点出完草稿后，`number_verifier` 扫一遍，确认所有数字都有对应的 exec_id 和 cell。

10. **HumanReview** 节点计算审批载荷：包含 graph_id、state 的哈希、报告摘要、所需角色（因为金额 720 不大，preparer 就能批），用 HMAC 签名后发飞书。然后图停住，把整个 state 存进 PostgresSaver。
11. 审批人收到飞书消息，点"同意"。后端 webhook 验签，调 `graph.update_state(config, {"approval_status": "approved"})`，然后 `graph.invoke(None, config)` 继续跑。
12. **Execute** 执行 Saga：第一步在财务系统记一笔"本次审计完成"的账；第二步发飞书通知申请人。两步都有对应的补偿函数——如果第二步失败，第一步会被撤销。每一步的幂等键是 `sha256(graph_id + step_name)[:16]`，所以即使 Worker 崩溃重启，已完成的步骤也不会重跑。
13. **Notify** 返回最终报告给前端。

与此同时，整条链路上的每个动作都被记进了三个地方：

- 审计日志：who 在 when 调了 what 工具，input hash 和 output hash 是什么
- Langfuse / OpenTelemetry：完整的 trace，包括 token 消耗和延迟
- Lineage 追踪器：报告里的 `720` 这个数字对应的 sources 是 `[sandbox#cell=OVERRUN_AMOUNT, sql#SQL-001#col=travel_budget, doc#REIMB-001#page=1]`

## 五个工程难题对应的模块

整个项目可以按"五个痛点 + 横切关注点"来组织。先看痛点：

| 痛点 | 主要代码 | 文档 |
|---|---|---|
| 数字不能错 | `sandbox/runner.py`、`sandbox/number_verifier.py` | `01_sandbox_guide.md` |
| SQL 要安全 | `sql_agent/` 下整个目录 | `02_text_to_sql_guide.md` |
| 权限要严 | `auth/` 下整个目录 | `03_rbac_token_passthrough.md` |
| 流程要可控 | `graph/` 下整个目录 | `04_graph_hitl_saga.md` |
| 文档要读懂 | `rag/` 下整个目录 | `05_doc_rag_layout.md` |

横切关注点（每个痛点都会用到，但独立成模块）：

| 关注点 | 代码 | 文档 |
|---|---|---|
| 评测和可观测 | `evals/`、`observability/` | `06_evals_observability.md` |
| 数据血缘和红队测试 | `lineage/`、`evals/redteam_suite.py` | `07_lineage_redteam.md` |

## 代码的整体组织风格

有几个约定贯穿整个代码库，了解了你读起来会快很多：

**每个子包（`sandbox/`、`sql_agent/` 等）都独立于其他子包。** 也就是说，你可以只装 pandas 就能跑 sandbox，不需要装 PaddleOCR。这是通过"延迟 import"做到的——如果一段代码需要 langchain 才能跑，那个 import 写在函数内部，而不是文件顶部。好处是单元测试不需要装全套依赖。

**配置统一走 `config.py`。** 所有环境变量（API key、数据库 URL、HMAC 密钥）都在这一个文件里集中读。其它模块从 `get_settings()` 拿。这样改配置只改一处。

**模型调用统一走 `config.get_llm()`。** 内部是 `ChatOpenAI(base_url=..., api_key=...)`。切供应商就改 `.env` 里的 `OPENAI_BASE_URL`。

**所有对外工具都在 `tools/` 下。** 一个工具做一件事，入口先调 `require_scope(...)` 检查权限，返回前包一层 `<tool_result untrusted>` 标签（防提示注入）。大模型看到的工具 API 是受控的。

**状态通过 TypedDict 定义。** `graph/state.py` 里的 `AgentState` 就是整个流程的"真源"——它是什么形状，LangGraph 的 checkpoint 就存什么形状。

## 如果你要动手改

几个常见的改动场景：

**想换个 LLM 供应商**：改 `.env` 里的 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`。如果是自建的 OpenAI 兼容服务，还要改 `FIN_LLM_MODEL_REASONING` 和 `FIN_LLM_MODEL_LIGHT` 告诉我们用哪个模型名。

**想加一个新的业务指标**（比如"资产负债率"）：在 `semantic_layer.py` 的 YAML 里加一条 measure 定义。加完之后 LLM 就能用 `{{ measure:资产负债率 }}` 来引用，不用改代码。详见 `docs/02`。

**想加一个新工具**：在 `tools/` 下建一个文件，函数开头调 `require_scope("xxx")`，返回前用 `wrap_untrusted(...)` 包装。然后在 `graph/nodes.py` 或 Planner 的提示词里让 LLM 知道有这个工具。

**想上真实的 Postgres 数据库**：把 `.env` 里的 `FIN_DB_URL` 从 SQLite 改成 `postgresql+psycopg://...`。`executor.py` 和 `checkpoint.py` 会自动走 Postgres 路径，并且启用 Row-Level Security 的配置。

## 还没做完的部分

为了坦诚，这里列一下已经动了但还没做完的地方：

- **`rag/layout.py`** 的 PaddleOCR 接入只是骨架——真正跑它要装 `paddlepaddle` 和 `paddleocr`，这两个包比较重，`environment.yml` 里注释掉了。当前的 `analyze_pdf` 返回一份内置的假报销单数据，够跑 demo 用。
- **`rag/reranker.py`** 里的 bge-reranker-v2-m3 同理——真要用请装 FlagEmbedding，当前会回退到"粗排截断"。
- **`sandbox/e2b_runner.py`** 只是骨架，生产要填 e2b SDK 的真实调用。
- **`observability/otel_setup.py`** 和 **`observability/semantic_cache.py`** 都是骨架，需要对接真实的 OTel Collector 和 Redis。

这些我都在对应文件顶部注明了"这里是骨架"。

## 下一步读什么

如果你的时间只有两个小时，我推荐这样读：

1. 先读 `docs/01_sandbox_guide.md`——这是整个项目最核心的想法（让 LLM 不写数字）。
2. 再读 `docs/04_graph_hitl_saga.md`——把主流程、审批、回滚这些串起来。
3. 最后回到各模块文档和 `examples/`，结合代码把关键链路跑一遍。

如果时间充裕，就按 00-08 的顺序读，每一篇 20 分钟左右。

要深入某一块的细节，直接看代码。我给每个关键函数都写了中文注释，注释里会说明"这么写是为了什么"，以及"生产应该怎么改"。
