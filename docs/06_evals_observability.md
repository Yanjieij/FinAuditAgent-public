# 06 · 怎么评测它、怎么监控它、怎么控制它的成本

这一篇讲 LLM 系统的三件"周边事"：评测、可观测、成本控制。

这三件事在传统后端开发里也有，但在 LLM 系统里特别重要。原因是：**LLM 系统跑起来不代表它工作正常**。代码没崩、接口返回了 200，但它给出的答案可能是错的、是胡编的、是过期的。你需要一套机制持续检验"它还在正确工作"。

## 为什么 LLM 系统需要评测

传统后端你有单元测试——给定输入 X，期望输出 Y。跑个 pytest，挂了就修。

LLM 系统的"正确"不是二值的：

- 同一个问题，同一个 prompt，同一个模型——今天答对了，明天可能答错（模型版本漂移、上游数据变）
- "错"的标准不清晰——答案措辞不同但意思一样算对吗？数字对但格式不对算对吗？
- 故障是静默的——代码不会崩，只是答案错了，用户可能 3 个月后才发现

所以你需要一套**golden set**（黄金集）——一批典型问题 + 期望答案，每次改代码、改 prompt、换模型前后都跑一遍，对比指标。**指标回退就阻断**。

## 我们的评测框架

分四个维度：

**一、Text-to-SQL 评测**。问题 → 系统生成的 SQL → 执行结果 → 对比期望。

**二、RAG 评测**。问题 → 检索到的片段 + LLM 基于片段的回答 → 看答案是否 grounding 在证据里。

**三、端到端评测**。完整的报销单审核流程 → 看各环节的产出是否合格。

**四、安全（红队）评测**。专门的攻击样本 → 看是否被拦住。

下面一个个说。

### Text-to-SQL 的黄金集

在 `evals/datasets/text2sql_golden.jsonl`：

```json
{"id": "t2s-001",
 "question": "市场部 2025 年 1-3 月的营销费用合计是多少？",
 "gold_sql": "SELECT SUM(amount) FROM fact_expense JOIN dim_department ...",
 "gold_answer": 1050000}
```

每条样本包含：问题、期望的 SQL、期望的答案。有些样本是 `should_refuse: true`——系统应该拒绝（比如"删除所有费用记录"）。

### Text-to-SQL 的指标

**execution_match**（执行结果匹配）：拿系统生成的 SQL 去跑，结果集和 gold 对比（无序比较）。

```python
def execution_match(predicted_df, gold_df):
    if len(predicted_df) != len(gold_df):
        return 0.0
    pred = sorted(tuple(r) for r in predicted_df.values.tolist())
    gold = sorted(tuple(r) for r in gold_df.values.tolist())
    return 1.0 if pred == gold else 0.0
```

**syntactic_em**（语法精确匹配）：空白归一化后 SQL 字符串相等。比 execution_match 更严格——同样的结果、不同的写法会算错。一般只用 execution_match，syntactic_em 做参考。

**refuse_rate**：对 `should_refuse=True` 的样本，系统拒绝的比例。

阈值：execution_match 至少 80%，低于则阻断 PR。

### RAG 的评测

RAG 最常用的评测框架是 **RAGAS**。它提供几个指标：

- **faithfulness**：答案的每个断言是否能在检索到的证据里找到支持。不能 → hallucination。
- **answer_relevance**：答案是否真的回答了问题（有些答案虽然没编，但跑题）。
- **context_precision**：检索到的 context 里有多少真的相关（噪声多少）。
- **context_recall**：应该检索到的相关 context 里，实际召回了多少。

RAGAS 的实现依赖用 LLM 做判断（LLM-as-judge）——它用另一个 LLM 来打分。成本稍高，但指标意义明确。

我们在 `evaluators.py` 里实现了一个简化的 `faithfulness_lite`——不依赖 LLM，用规则检查答案里的关键 token 是否在证据里出现。精度不如 RAGAS 但便宜。

另外对引证专门做一个 **citation_iou**：LLM 输出的 `[citation:...bbox=...]` 和 gold 的 bbox 做 IoU，超 0.5 算命中。

### 端到端评测

端到端测试的样本结构（`evals/datasets/e2e_tasks.jsonl`）：

```json
{"id": "e2e-001",
 "question": "这张报销单是否超预算？给出审计意见。",
 "expected": {
   "has_citation": true,
   "has_evidence_pointer": true,
   "has_hitl_step": true
 }}
```

跑完整流程后检查：

- `has_evidence_pointer`：Drafter 的输出里是否有 `[[exec_id=...#cell=...]]`
- `has_citation`：输出里是否有 `[citation:...]`
- `step_count`：跑了多少个节点（异常多意味着图可能在兜圈）
- LLM-as-judge：用 GPT-4o 打分"这份审计意见是否合理"

### CI gating：阈值阻断

三个指标都有阈值：

```python
THRESH = {
    "t2s_exec_match": 0.80,
    "rag_faithful":   0.90,
    "redteam_block":  1.00,  # 红队必须 100%
}
```

CI 上：

```yaml
- run: python evals/run_eval.py
```

脚本退出码 0 通过，非 0 阻断 PR。开发者改了一个 prompt，跑评测发现 execution_match 降到 75%——PR 合不了，必须先修。

### 不同分支策略

- **main 分支**：严格阈值。任何指标回退都阻断。
- **feature 分支**：宽松一点，打警告但不阻断。
- **hotfix 分支**：只看安全相关的阈值（红队和权限）。

## 红队测试

这块在 `docs/07` 里专门讲。简单说：维护一组"攻击样本"，每个 PR 跑一遍，拦截率必须 100%。

## Langfuse：LLM 专属的可观测平台

### 为什么需要专门的 trace 工具

普通后端用 Datadog、New Relic、SkyWalking 就够了——跟 HTTP 请求、DB 查询、外部调用。

LLM 系统里有几样东西这些工具抓不到：

- 每次 LLM 调用的 prompt 和 response 是什么
- 消耗了多少 token，花了多少钱
- 调用链里哪一步用了什么 prompt 模板版本
- 同一个 prompt 不同时候的输出对比

专门的 LLM 观测平台对这些优化得更好。主流几个：

**Langfuse**：开源，可以自托管。我们优先选这个——金融场景合规要求"数据不能出公司"，能自托管很重要。

**LangSmith**：LangChain 家的，功能最全。SaaS，数据要传到他们那。

**Helicone**、**Phoenix / Arize**、**Braintrust**：各有侧重，主流但功能不如 Langfuse / LangSmith。

### 接入

`observability/langfuse_setup.py`：

```python
def init_langfuse():
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return False  # 没配 key，降级为 no-op
    from langfuse import Langfuse
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    return True

def trace(name=None):
    """装饰器：函数调用自动上报到 Langfuse。"""
    try:
        from langfuse.decorators import observe
        return observe(name=name)
    except ImportError:
        return lambda fn: fn  # 降级
```

用法：

```python
from fin_audit_agent.observability.langfuse_setup import init_langfuse, trace

init_langfuse()

@trace(name="sql_pipeline")
def run_sql_pipeline(question):
    ...
```

跑一次之后，Langfuse Web UI 里能看到完整的 trace 树——每个节点、每次 LLM 调用、每个工具调用都有对应的 span，带输入输出、token 数、延迟。

### 要记录什么

每个 span 的属性建议填：

- `user.sub`、`user.role`、`tenant`：上下文
- `model`、`tokens.in`、`tokens.out`、`cost.usd`：成本
- `node`、`tool`、`sql_id`、`exec_id`：业务语义
- `verdict`、`decision`：这一步的决策结果

Langfuse 支持按这些属性做聚合查询，比如"过去 7 天，market 部门的用户平均延迟是多少"。

### 数据集联动

Langfuse 还能做一件事：**把生产 trace 沉淀成评测数据集**。

流程是：

1. 每天从生产 trace 里抽样 50 条有代表性的
2. 人工标注期望答案
3. 沉淀到 Langfuse 的 dataset
4. 之后每次发版跑这个 dataset 做回归

这样评测集能**持续跟上真实流量**——它不是一开始写死的，而是随着业务演化不断扩充。

## OpenTelemetry：系统级 trace

### 为什么还要这个

Langfuse 管 LLM 视角，OpenTelemetry（以下简称 OTel）管系统视角。

OTel 是 CNCF 的标准，定义了一套跨语言的 trace 规范。主流 APM 工具（Jaeger、Tempo、Zipkin、Datadog）都支持 OTel 协议。

在金融公司，通常 SRE 团队已经建好 OTel / Jaeger 基建用于后端服务的 tracing。我们把 Agent 的 trace 也接入进去，好处是：

- LLM 调用的系统 span 能和前置的 HTTP、后续的 DB 串起来——"这个请求从用户点按钮到最后落库，每一步耗时"
- 可观测告警（延迟、错误率）用同一套
- 合规审计工具能统一查

### 接入

`observability/otel_setup.py`：

```python
def init_otel(service_name="fin-audit-agent"):
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    # 本地用 ConsoleSpanExporter 打印
    # 生产用 OTLP 导出到 Jaeger / Tempo
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter()
    else:
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
```

用法：

```python
from fin_audit_agent.observability.otel_setup import with_span

with with_span("execute_saga", graph_id=thread_id) as span:
    # 跑 saga
    span.set_attribute("saga.step_count", len(saga.steps))
```

### Langfuse vs OTel 不冲突

它们互补：

- 同一次 LLM 调用在 Langfuse 和 OTel 里都有 span
- Langfuse 上有更丰富的 LLM 元数据（token、prompt 版本）
- OTel 上能看到上下游的系统调用（HTTP、DB）

两边的 trace 可以通过 `trace_id` 关联——有些 Langfuse 集成也支持 OTel 协议，能自动贯通。

## 成本控制

### 为什么重要

一个财务审计请求大概长这样的调用链：

- Planner 调一次 LLM（reasoning 模型）
- 3 个子节点各调 1-3 次 LLM（mixed）
- Drafter 调一次（reasoning 模型，context 可能很大）
- 如果 verify 不通过，Drafter 重试最多 3 次

保守估算一个请求 5-10 次 LLM 调用。如果每次都用最贵的模型（GPT-4o / Claude Opus），一个请求 0.3-1 美元。量大了一天烧几万块。

### 模型路由

第一个省钱策略：**轻任务用便宜模型，重任务用贵模型**。

便宜任务：意图分类、Schema Linking、query rewriting、embedding、rerank。这些对模型能力要求不高。

贵任务：Planner（要拆分复杂任务）、Drafter（要写严谨报告）。

我们在 `observability/cost_budget.py` 里提供路由函数：

```python
def choose_model(task_kind: str) -> str:
    if task_kind in {"classify", "embed", "rerank", "schema_link", "ner"}:
        return settings.model_light  # 比如 gpt-4o-mini / deepseek-chat
    return settings.model_reasoning   # 比如 gpt-4o / claude-opus
```

便宜模型和贵模型的价格差通常 10-20 倍。能切就切，总成本大概降 60-80%。

### 单请求预算门

第二个策略：**单请求有硬上限，超了就终止**。

```python
@dataclass
class Budget:
    max_tokens: int
    max_usd: float
    used_tokens: int = 0
    used_usd: float = 0.0

    def charge(self, tokens=0, usd=0.0):
        self.used_tokens += tokens
        self.used_usd += usd
        if self.used_tokens > self.max_tokens:
            raise BudgetExceeded(...)
        if self.used_usd > self.max_usd:
            raise BudgetExceeded(...)
```

用法：

```python
def start_budget():
    b = Budget(max_tokens=100000, max_usd=0.50)
    _budget_var.set(b)
    return b

# 每次 LLM 调用后：
current_budget().charge(tokens=resp.usage.total_tokens, usd=price_usd(...))
```

超了就抛 `BudgetExceeded`，上层节点捕获后走降级路径——比如返回"请求复杂度超过当前配额，请联系管理员"。

### 价格表维护

`cost_budget.py` 里内置了一张价格表：

```python
_PRICE_PER_1K_TOKENS = {
    "gpt-4o":         {"in": 2.50, "out": 10.00},
    "gpt-4o-mini":    {"in": 0.15, "out": 0.60},
    "deepseek-chat":  {"in": 0.14, "out": 0.28},
    "qwen-turbo":     {"in": 0.05, "out": 0.10},
}

def price_usd(model, in_tokens, out_tokens):
    tier = _PRICE_PER_1K_TOKENS.get(model, {"in": 0, "out": 0})
    return (in_tokens * tier["in"] + out_tokens * tier["out"]) / 1000
```

价格随时间变动，这张表要定期更新。生产也可以从 Langfuse 的 price tier 拉取（Langfuse 自己维护一份）。

## 语义缓存（骨架）

第三个省钱策略：**重复问题别每次都跑完整流程**。

场景：同一个用户一天内反复问"Q4 市场部费用率"——第一次跑完 SQL + 沙箱 + LLM，答案是 15.3%；下次再问，**底层数据没变**，答案应该是一样的。

朴素缓存（key 是 query 字符串）不够——用户可能问"Q4 市场部的费用率"或者"市场部 Q4 费用率"，字符串不同但意思一样。

**语义缓存**的做法是 key 用 embedding——如果新 query 的 embedding 和历史某个 query 相似度 > 0.95，就返回缓存答案。

### 实现骨架

`observability/semantic_cache.py`：

```python
class SemanticCache:
    def get(self, query, user_tenant, threshold=0.95):
        # 生产实现：
        # emb = bge_m3.encode(query)
        # hits = redis.ft(index).search(
        #     f"@user_tenant:{{{user_tenant}}}",
        #     query_vector=emb, top_k=1
        # )
        # if hits and hits[0].score > threshold:
        #     return CacheEntry(...)
        return None
    
    def put(self, query, answer, user_tenant):
        # 生产实现：存 embedding + metadata + TTL
        pass
```

当前是骨架。生产要：

- Redis 7 带 Vector Search 模块（或者单独部署 Milvus）
- bge-m3 做 embedding
- TTL 设置 1 小时（财务数据每日更新）
- **按 `user_tenant + role` 分 key**——不同权限的用户不能共享缓存

### 几个陷阱

**写操作绝不缓存**。审批、打款、记账这些永远不能从缓存返回。

**时间敏感的问题要小心**。"今天的"、"Q1 的"这种时间限定词，缓存 1 小时可能就过期了。

**权限敏感的问题要分 key**。同样问"部门费用"，CFO 看到的和普通员工看到的不一样（RLS 过滤结果不同）。

## 一个生产化清单

如果你要把这个项目上生产，观测这块建议按下面的清单过一遍：

- 所有 LLM 调用通过 Langfuse（就算 mock 测试也接上，积累评测数据）
- 所有节点加 OTel span，属性带 `user / role / tenant / tokens / cost`
- 每个 PR 触发评测，阈值不过不能合
- 每周用生产 trace 抽 50 条做 LLM-as-judge 回归
- 每月更新评测集（加 20 条真实线上 hard case）
- 模型路由 + 预算门 100% 覆盖关键调用
- 成本仪表盘按 `tenant / action / model` 分组
- 告警规则：单请求成本超阈值告警；日均成本突增告警

## 常见问题

**问：评测集多大合适？**

答：刚开始 30-50 条就够跑 CI。真正的长期资产是"随着生产流量持续扩充"——每个月加 20 条最难的线上 case，一年后你有 200+ 条覆盖各种场景的黄金集。

**问：LLM-as-judge 靠谱吗？**

答：不 100% 靠谱，但比纯人工快很多。我们的经验：用 GPT-4o 当 judge，和人工标注的一致性大概 85-90%。重要决策还是要复核，但做回归够用。

**问：trace 会不会记录 PII？**

答：这是个重要问题。Langfuse / OTel 的 trace 默认会包含 prompt 和 response，可能含 PII。两个策略：一是在上报前做脱敏（就是 `auth/redactor.py` 做的事），二是在 Langfuse 后端配置 PII 过滤规则。我们的 LLM 调用在进入 LLM context 之前已经脱敏过，所以 trace 上只有脱敏后的数据。

**问：怎么判断评测集"够好"？**

答：三个标准：
- 覆盖常见场景（80% 的流量）
- 覆盖已知 hard case（之前出过问题的）
- 每条样本的期望答案经过至少两个人复核

**问：Langfuse 自托管贵吗？**

答：不贵。一个 Postgres + 一个 Langfuse 后端进程，中小公司一台机器就够了。大公司加个 ClickHouse 做 trace 长期存储。

**问：语义缓存命中率一般多少？**

答：看场景。内部财务问答类 30-50% 很正常；开放域问答（比如客服）20% 左右。命中率即使 30%，也能省下大量成本。

## 要深入代码的话

```
fin_audit_agent/observability/
├── langfuse_setup.py        # Langfuse 初始化 + trace 装饰器
├── otel_setup.py            # OpenTelemetry 初始化
├── cost_budget.py           # 模型路由 + 预算门 + 价格表
└── semantic_cache.py        # 语义缓存（骨架）

evals/
├── datasets/
│   ├── text2sql_golden.jsonl
│   ├── rag_faithfulness.jsonl
│   └── e2e_tasks.jsonl
├── evaluators.py            # 各指标实现
├── redteam_suite.py         # 红队样本
└── run_eval.py              # CI 入口
```

推荐阅读顺序：

1. `evals/evaluators.py`——了解各指标怎么计算
2. `evals/run_eval.py`——评测流程怎么跑、阈值怎么阻断
3. `observability/cost_budget.py`——成本控制
4. `observability/langfuse_setup.py`——Langfuse 接入

Demo 直接跑：

```bash
python evals/run_eval.py
```

看各指标输出和 pass/fail 状态。
