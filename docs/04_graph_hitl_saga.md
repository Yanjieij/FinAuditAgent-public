# 04 · 主流程编排、人工审批、事务回滚

这一篇讲整个 Agent 的"骨架"——主流程怎么组织、人工审批怎么接入、写操作失败怎么回滚。

涉及三个词，先说清楚：

**FSM**（Finite State Machine，有限状态机）：把一个流程拆成若干"节点"，节点之间的跳转规则是提前固定的。对比"自由规划"的做法，FSM 是"确定性流程"。

**HITL**（Human-in-the-Loop，人在回路）：在 Agent 执行过程中的某些关键点，暂停下来等真人审批，审批通过再继续。

**Saga**：一种处理"多步有副作用操作"的模式。如果中间某步失败，按反向顺序调用"补偿函数"把之前做过的步骤撤销。

这几个概念是 Agent 工程化里最核心的一套"业务韧性"工具，我们一个个讲。

## 起点：为什么纯 ReAct 在金融场景不行

先聊一下 ReAct，因为这是最主流的 Agent 实现方式。

**ReAct**（Reason + Act）的核心循环是：

```
LLM 看到问题 → 决定调哪个工具 → 调用工具 → LLM 看到结果 → 决定下一步...
```

LLM 每一步都自己决定下一步。这种方式很灵活——Agent 能应对没见过的任务。很多开源框架（LangChain 的 AgentExecutor、AutoGen 的 conversable_agent）都基于 ReAct。

但在金融审计场景里，它有三个致命问题：

**问题一：循环失控**。LLM 可能连续调 10 次工具才收敛——或者根本不收敛，一直兜圈。每次调用都花钱（token + 工具成本），一个请求可能烧光预算。对用户来说延迟也难接受。

**问题二：写操作不能原子化**。假设 Agent 执行时真的产生了副作用（在财务系统里记了一笔账），接下来它决定再发一条飞书通知。通知发送失败。ReAct 没有内建的"回滚"机制——那笔记账已经写进去了，不会自动撤。

**问题三：审批没地方插**。监管要求"涉及金额的决策必须真人审批"，但 ReAct 的流程是 LLM 自由决定的，**哪一步是审批点是不确定的**。你没办法说"在 XX 节点之前停下来"，因为不存在"XX 节点"。

## 我们的折中方案

混合 FSM + bounded ReAct。

顶层流程是一条**钉死的 FSM**：

```
Intake → Clarify → Planner → {DataFetch, DocRAG, Analyze} → Drafter
                                                              ↓
                                                        HumanReview（停住）
                                                              ↓
                                                        Execute（Saga）
                                                              ↓
                                                           Notify → END
```

每个节点的职责清晰、跳转规则确定。关键节点（比如 HumanReview）有固定位置，可以精确插入审批。

但**在 Analyze 这个节点里面**，我们允许 LLM 自由调工具——这是 ReAct 发挥的空间。不过我们设了上限（最多 5 次迭代），防止失控。

这种设计的核心直觉是：**复杂问题的"分析"步骤适合探索性的 ReAct，但整体业务流程需要确定性的 FSM**。

## FSM 的实现：LangGraph

我们用 LangGraph 来写 FSM。LangGraph 是 LangChain 生态里专门做 Agent 编排的库，核心概念是 `StateGraph`。

### 最基本的用法

```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

# 1. 定义 State（状态）
class MyState(TypedDict):
    question: str
    answer: str

# 2. 写节点函数（接 state，返 state 的部分字段）
def node_intake(state):
    return {"question": state["question"]}

def node_answer(state):
    return {"answer": f"Echo: {state['question']}"}

# 3. 构造 Graph
g = StateGraph(MyState)
g.add_node("intake", node_intake)
g.add_node("answer", node_answer)
g.add_edge(START, "intake")
g.add_edge("intake", "answer")
g.add_edge("answer", END)
graph = g.compile()

# 4. 运行
result = graph.invoke({"question": "hello"})
# result = {"question": "hello", "answer": "Echo: hello"}
```

这就是一个两节点的 FSM。`State` 是这个 graph 里流动的数据结构，每个节点能读写它。

### 条件边（分支）

真实业务里不是简单的线性流程。LangGraph 提供"条件边"——一个节点执行完后，根据 state 决定下一步去哪。

```python
def route_after_drafter(state):
    if state["verify_report"]["ok"]:
        return "human_review"
    else:
        return "drafter"  # 校验没通过，重写

g.add_conditional_edges(
    "drafter",
    route_after_drafter,
    {"drafter": "drafter", "human_review": "human_review"},
)
```

Drafter 出完报告，看 `verify_report` 是否通过。通过就去 HumanReview，不通过就回到 Drafter 重写。

### AgentState 的设计

我们的 `AgentState`（`graph/state.py`）比 demo 复杂得多：

```python
class AgentState(TypedDict, total=False):
    # 基础
    messages: Annotated[list, add_messages]
    question: str
    iterations: int

    # Planner 产出
    plan: list[str]

    # 三个子图的结果
    sql_results: list[SQLQueryRecord]
    rag_chunks: list[RAGChunkRef]
    sandbox_execs: list[dict]

    # Drafter
    draft: str
    verify_report: dict

    # HITL
    approval_token: str | None
    approval_status: Literal["pending", "approved", "rejected", "timeout", ""]
    approver_role_required: str

    # Saga
    saga_log: list[SagaStep]

    # 终态
    final_answer: str
    verdict: Literal["ok", "retry", "need_human", "failed", ""]
```

几个要点：

**用 `TypedDict`，不是 Pydantic BaseModel**。因为 LangGraph 默认对 TypedDict 有更好的支持，序列化和 reducer 机制都是为 TypedDict 设计的。

**`Annotated[list, add_messages]`**。这是 LangGraph 的"reducer"机制。普通字段节点返回 `{"question": "xxx"}` 会覆盖，但 `messages` 用了 reducer 之后，节点返回 `{"messages": [HumanMessage(...)]}` 会**追加**。这对对话历史很方便。

**`total=False`**。意思是所有字段都可选。这样节点可以只返回部分字段，LangGraph 会 merge。

## 检查点（Checkpoint）

前面说的 FSM 是内存里的。真实生产里，流程可能跑很久（审批拖好几天），我们不能把整个 state 一直吃内存。LangGraph 提供 `Checkpointer`——每跑过一个节点就把 state 存一次。

我们用 Postgres 做 checkpointer。在 `graph/checkpoint.py`：

```python
def make_checkpointer(db_path=None, backend=None):
    if backend == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver
        saver = PostgresSaver.from_conn_string(get_settings().db_url)
        saver.setup()  # 幂等建表
        return saver
    elif backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(path, check_same_thread=False)
        return SqliteSaver(conn)
    # ...
```

默认按配置自动选：本地开发用 SQLite，生产用 Postgres。

### Checkpoint 的三个价值

**一是崩溃恢复**。Worker 挂了，重启之后可以从上一次的 checkpoint 继续，不用从头跑。

**二是审批等待**。HumanReview 节点停住之后，整个 state 落 Postgres。审批拖 3 天也没事——Worker 不用干等，节省资源。审批回来再触发继续。

**三是可观测**。任何时候都能 `graph.get_state(config)` 查当前状态。调试时很有用。

### `thread_id`：会话标识

Checkpointer 需要一个标识来区分不同的"会话"。这就是 `thread_id`：

```python
config = {"configurable": {"thread_id": "audit-2025-04-21-001"}}
result = graph.invoke({"question": "..."}, config=config)

# 下次继续：
next_result = graph.invoke({"question": "..."}, config=config)  # 同 thread_id → 接着上次
```

`thread_id` 可以是 UUID，也可以是有业务含义的 ID（比如 `tenant:user:request_id`）。

## HITL：interrupt_before + 审批载荷

### 怎么让图停下来

LangGraph 提供 `interrupt_before` 参数，告诉它"跑到这个节点之前停下来"：

```python
graph = g.compile(
    checkpointer=PostgresSaver(...),
    interrupt_before=["execute"],  # 停在 execute 前
)
```

之后调 `graph.invoke(...)`：

- 图会跑到 `execute` 节点**之前**
- 把当前 state 存进 checkpointer
- 立即返回（不阻塞）

返回的 state 里有所有前序节点的产出——plan、sql_results、draft 等。审批人就基于这些信息做决策。

### 恢复执行

审批人同意之后：

```python
# 更新 state：设 approval_status 为 approved
graph.update_state(config, {"approval_status": "approved"})

# 继续跑（invoke 的第一个参数传 None 表示"不新增输入，接着跑"）
final = graph.invoke(None, config=config)
```

LangGraph 会从上次停的地方继续，一路跑到 END。

### 审批载荷和 HMAC 签名

光让图停下来还不够，审批人怎么知道要批什么？我们构造一个审批载荷，带 HMAC 签名发出去。

代码在 `graph/hitl.py`：

```python
@dataclass
class ApprovalPayload:
    graph_id: str              # checkpoint 的 thread_id
    state_hash: str            # 当前 state 的 hash，防篡改
    draft_preview: str         # 最多 2000 字的草稿摘要
    required_role: str         # preparer / approver / cfo
    amount: float              # 涉及金额
    expires_at_ms: int         # 24 小时后过期

def build_approval_payload(graph_id, state, required_role, amount):
    payload = ApprovalPayload(...)
    sig = HMAC(secret, json(payload))
    return payload, sig
```

然后把 `payload + sig` 塞进飞书消息发给审批人。飞书消息里带个"同意"按钮，点击触发我们的 webhook。

### Webhook 回调

```python
@app.post("/webhook/approval")
async def approval_webhook(body: dict):
    payload_dict, sig = body["payload"], body["sig"]

    # 验签 + 过期检查
    if not verify_signature(payload_dict, sig):
        return {"ok": False, "reason": "invalid signature or expired"}

    # 权限检查：审批人角色是否符合
    if current_user().role != payload_dict["required_role"]:
        return {"ok": False, "reason": "insufficient role"}

    # 更新 state 继续跑图
    config = {"configurable": {"thread_id": payload_dict["graph_id"]}}
    graph.update_state(config, {"approval_status": "approved"})
    graph.invoke(None, config=config)
    return {"ok": True}
```

### 为什么 HMAC 签载荷

三个作用：

**防篡改**：审批人看到的 `amount = 12000`，验签时 state 里还是 12000。中途有人改了载荷，验签失败。

**防重放**：载荷里带 `expires_at_ms`，24 小时后即使重发也拒。

**防越权**：`required_role = "approver"`，如果审批人是 "preparer"，我们能拒。

### 升级阶梯

金额不同，需要不同层级的审批。在 `hitl.required_role_for_amount`：

```python
def required_role_for_amount(amount: float) -> str:
    if amount <= 10_000:
        return "preparer"
    if amount <= 100_000:
        return "approver"
    return "cfo"
```

Drafter 节点算出金额，HumanReview 节点按这个规则决定给谁发。

升级阶梯的配置理想情况是走 OPA 策略引擎（`auth/policy_engine.py`）——改阈值不需要改代码，SRE 改个 Rego 规则就行。我们这里写死是为了简化 demo。

### 超时处理

生产里审批人可能忘了回。我们的策略：24 小时没响应自动置为 `timeout`，走拒绝路径。

实现方式：一个定时任务扫所有 `approval_status=pending` 的 thread，看 `expires_at_ms` 是不是过了。过了就 `update_state(config, {"approval_status": "timeout"})`，然后 `invoke(None, config)`。图会走拒绝路由到 END。

## Saga：写操作失败的回滚

先看整体路径：正向按顺序执行三步；任何一步失败就按相反顺序调用每步成对的补偿函数；幂等键保证 Worker 崩溃重启也不会重跑。

![Saga 补偿事务](images/saga-compensation.drawio.png)

### 场景

审批通过之后，Execute 节点要做几件有副作用的事情：

1. 在财务系统的 ledger 表里记一条新账目
2. 更新工单状态
3. 发飞书通知申请人

假设前两步都成功了，第三步飞书接口挂了。这时候我们需要"撤销"前两步——把 ledger 里那条记录删掉（或者标记为无效），工单状态改回去。

如果这三步是在同一个数据库里，用一个 DB 事务就搞定了——commit 之前任何一步 rollback，所有改动都撤销。但它们跨系统：ledger 是财务系统、工单是另一套、飞书是第三方 API。**跨系统无法用单机事务**。

### 为什么不用 2PC

分布式事务的老办法是 2PC（Two-Phase Commit，两阶段提交）：

```
Phase 1 (prepare): 协调者问所有参与者"能提交吗？" → 都回 yes → 所有人进入 ready 状态
Phase 2 (commit): 协调者说"commit" → 所有人提交
```

理论上能保证原子性，但：

- 需要所有参与者都支持 2PC（飞书 API 肯定不支持）
- 需要分布式锁、协调者、可用性模型复杂
- 单个参与者挂了，其它人可能永远 blocked 在 ready 状态

2PC 在互联网金融场景被慢慢淘汰了。取而代之的是 Saga 模式。

### Saga 的核心思想

**每一步都是一个独立的本地事务，立即 commit**。如果某一步失败，按**反向顺序**调用"补偿函数"，补偿函数负责撤销之前步骤的效果。

用数学符号表示：

```
正向流程：T1, T2, T3, T4, T5
         如果 T3 失败：
         按 T2 → T1 的顺序调补偿函数 C2, C1
```

`T_i` 和 `C_i` 都是开发者自己写的（框架不知道你的业务是什么意思，没法自动撤销）。

### 关键：幂等键

考虑一个麻烦场景：T1、T2 成功了，T3 执行时 Worker 崩了。Worker 重启，从 checkpoint 恢复，重新执行 Saga。

这时候 T1、T2 要不要再执行一次？**一定不能**。否则 ledger 里会多出两条重复的记账。

解决办法是**幂等键**。每一步执行时带一个稳定的 key：

```python
def idempotency_key(graph_id: str, step_name: str) -> str:
    return sha256(f"{graph_id}:{step_name}".encode()).hexdigest()[:16]
```

同一个 `graph_id` + `step_name` 会得到同一个 key。第一次执行时，我们把 key 记到 `saga_log` 里标记为 "done"。重启重跑时，看到 key 已经 done，直接跳过。

```python
for step in self.steps:
    key = idempotency_key(self.graph_id, step.name)
    if key in done_keys:
        continue  # 这一步上次已经完成，跳过
    try:
        step.do(step.payload)
        completed.append(step.name)
    except Exception as e:
        # 失败，逆序补偿
        compensated = self._compensate(completed)
        return SagaResult(ok=False, ...)
```

`saga_log` 是 AgentState 的一部分，LangGraph checkpointer 会自动持久化。这样"幂等性"和"断点续跑"就是一套机制。

### 补偿函数的写法

理想情况下，补偿函数能完全撤销。比如：

```python
def record_ledger(payload):
    db.insert(ledger_row)

def compensate_ledger(payload):
    db.delete(ledger_row)
```

但很多场景做不到"完全撤销"。比如已经打款给对方账户了，你不能说"撤销"，只能**记一笔反向操作**：

```python
def compensate_payment(payload):
    # 不是删除已打款记录，而是登记一笔冲正
    db.insert(reverse_payment_row)
```

这样前向和反向都留痕，审计回放时能看到完整的链条。

### 代码结构

`graph/saga.py` 里 `Saga` 类的用法：

```python
saga = Saga(graph_id="thread-123")

saga.add(Step(
    name="record_ledger",
    do=lambda p: db.insert_ledger(p),
    compensate=lambda p: db.reverse_ledger(p),
    payload={"amount": 12000, "note": "审计通过"},
))

saga.add(Step(
    name="notify_feishu",
    do=lambda p: feishu.send(p["text"]),
    compensate=lambda p: feishu.send(f"上一条通知已撤销：{p['text']}"),
    payload={"text": "张三的报销单已通过审计"},
))

result = saga.run(existing_log=state.get("saga_log", []))
```

`existing_log` 是重启 resume 场景下的输入——如果上一次某些步骤已经 done，这些 key 在新的 run 里会跳过。

### 一个完整例子

看 `examples/06_saga_rollback.py`。脚本做两件事：

**第一轮：第 3 步故意失败**。

```python
saga = Saga("g1")
saga.add(Step("record_ledger", do_a, compensate_a))   # 成功
saga.add(Step("notify_feishu", do_b, compensate_b))   # 成功
saga.add(Step("update_ticket", do_fail, _noop))       # 故意 raise

result = saga.run()
# result.ok = False
# result.failed_step = "update_ticket"
# result.completed = ["record_ledger", "notify_feishu"]
# result.compensated = ["notify_feishu", "record_ledger"]  # 逆序！
```

**第二轮：模拟 resume**。

```python
# 假设上次"record_ledger"和"notify_feishu"已完成
existing_log = [
    {"step": "record_ledger", "status": "done", "idempotency_key": ...},
    {"step": "notify_feishu", "status": "done", "idempotency_key": ...},
]

# 重新跑一个不会失败的 saga
saga2 = Saga("g1")  # 同一个 graph_id
saga2.add(Step("record_ledger", ...))
saga2.add(Step("notify_feishu", ...))
saga2.add(Step("update_ticket", do_ok, _noop))

result = saga2.run(existing_log=existing_log)
# 前两步因为 idempotency key 匹配，直接跳过
# 只执行第三步
```

## 把三件事组合起来

单看 FSM、HITL、Saga 都不稀奇。强的是它们组合之后：

**FSM 提供"结构"**——流程有固定节点，审批有固定插入点。

**HITL 提供"暂停 + 恢复"**——图停在某个节点，state 落盘，等事件再继续。

**Saga 提供"原子性"**——写操作要么全做要么全不做。

**Checkpointer 把它们串起来**——state 持久化让 HITL 能等任意长时间；`saga_log` 作为 state 一部分，也享受 checkpoint 的断点续跑能力。

结果就是一个**在现实世界里真的能工作的 Agent**。它能应对：

- Worker 崩溃重启（checkpoint 恢复）
- 审批人拖了 3 天（checkpoint 持久化）
- 某步网络抖动失败（saga 补偿）
- resume 后重复调用的担忧（幂等键）

## 常见问题

**问：纯 FSM 会不会太僵化？**

答：我们在 Analyze 节点里留了 ReAct 的空间。真正需要"规划 + 探索"的业务（比如复杂审计报告生成），可以在那里让 LLM 自由发挥。顶层 FSM 管不越轨，子图管灵活。

**问：LangGraph vs Temporal / Cadence 选哪个？**

答：LangGraph 对 LLM 场景有更好的原生支持（流式输出、interrupt、子图）。Temporal / Cadence 是通用 workflow 引擎，功能更全（定时任务、child workflow、signal），但没有 LLM 专属特性。

混合方案：大多数 LLM 流程用 LangGraph；少数"写操作特别多、LLM 少"的子流程可以把 Execute 节点外包给 Temporal。

**问：HumanReview 停图期间，AgentState 存多久？**

答：按业务设定。我们默认 24 小时，到期自动 timeout。存储是 Postgres（或者 S3 归档更长期），空间不是问题。

**问：Saga 的补偿函数本身失败怎么办？**

答：难题。我们的策略：补偿失败不中断后面的补偿，但记告警。真实生产里这种情况要 oncall 人工介入——"T1 做了，T2 做了，T3 失败要补偿，结果 C2 也失败了"，这时候系统处于"脏"状态，必须人工对账。

**问：LangGraph 的 subgraph 并行支持如何？**

答：0.2 版本的并行还比较简单。我们 demo 里 DataFetch / DocRAG / Analyze 其实是**串行**的（builder.py 里用了 `add_edge` 链式）。真要并行，LangGraph 支持在一个 conditional_edge 里同时跳到多个节点、用 reducer 合并结果。生产要做这一步。

**问：Saga 能处理嵌套事务吗？**

答：嵌套的 Saga（Saga 里再嵌 Saga）理论可行，但很复杂——外层 Saga 失败时，内层 Saga 已经 commit 的"子 Saga"要全部补偿。Netflix 的 Conductor、Uber 的 Cadence 有相关模式。我们 demo 只做一层。

## 要深入代码的话

```
fin_audit_agent/graph/
├── state.py          # AgentState 定义
├── nodes.py          # 九个节点的实现
├── edges.py          # 条件路由函数
├── checkpoint.py     # Saver 工厂
├── hitl.py           # 审批载荷构造 + 验签
├── saga.py           # Saga 和幂等键
└── builder.py        # build_graph() 组装全图
```

测试：

- `tests/test_saga_compensation.py`：Saga 的各种失败场景
- `tests/test_graph_resume.py`：图的 interrupt + resume

Demo：

- `examples/04_graph_hitl_demo.py`：HITL 完整流程
- `examples/06_saga_rollback.py`：Saga 补偿 + 幂等 resume
- `examples/07_end_to_end_audit.py`：整个架构串起来

先读 `state.py` 了解数据形状，再读 `builder.py` 看图怎么组装，然后按自己感兴趣的节点深入。
