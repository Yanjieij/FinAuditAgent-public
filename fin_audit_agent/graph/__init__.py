"""痛点 4：LangGraph 确定性 FSM + HITL + Saga 补偿事务。

**为什么放弃纯 ReAct**（面试核心论点）：

    纯 ReAct = "LLM 在循环里决定下一步调哪个工具"。在金融场景有三个致命问题：
        1. **循环失控**：LLM 连续调工具可能烧光预算甚至引发数据库扫全表
        2. **无法回滚**：Execute 节点写了 3 步，第 4 步失败，前面 3 步不知道怎么撤
        3. **审批无处嵌**：人工审批需要在固定的节点暂停，自由规划下没有"固定节点"

**解法**：**混合 FSM + ReAct**
    - 顶层 FSM（StateGraph）把主流程"钉死"：``Intake → Clarify → Plan → {DataFetch || DocRAG} → Analyze → Draft → HumanReview → Execute → Notify``
    - 只在 Analyze 子节点里允许 **bounded ReAct**（最多 5 步）
    - Execute 用 **Saga pattern** 做补偿事务 + 幂等
    - HumanReview 用 ``interrupt_before``，中断后落 PostgresSaver，等审批回来再 resume

模块：
    - :mod:`state`      —— AgentState / saga_log / approval_token
    - :mod:`nodes`      —— 每个 FSM 节点的实现
    - :mod:`edges`      —— 条件路由 + 升级阶梯
    - :mod:`checkpoint` —— SqliteSaver / PostgresSaver 工厂
    - :mod:`hitl`       —— 审批载荷 HMAC 签名
    - :mod:`saga`       —— 补偿事务 + 幂等键
    - :mod:`builder`    —— 组装 + 编译
"""
