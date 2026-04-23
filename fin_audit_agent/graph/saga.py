"""Saga pattern 补偿事务 + 幂等键。

**场景**：
    Execute 节点要做 3 步有副作用的操作（例：记账 + 更新工单 + 发通知）。
    任何一步失败，前面已成功的步骤必须 **compensate**（补偿回滚），而不是留半成品。

**实现**：
    每步注册 ``(do, compensate, idempotency_key)``；Saga 依次执行，
    失败时按**逆序**调补偿函数。幂等键用 ``hash(graph_id + step_name)``，
    配合 :class:`AgentState.saga_log` 实现断点续跑不重放。

**面试关键点**（区别于 2PC）：
    - Saga 是 **最终一致性**，每步本地事务立即 commit；
    - 不需要分布式锁，适合跨系统（ERP / 消息 / 工单）编排；
    - 补偿函数不一定"撤销"，有时是"登记反向操作"（比如给已打款的做冲正）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Step:
    """Saga 中的一步。"""

    name: str
    do: Callable[[dict[str, Any]], dict[str, Any]]
    compensate: Callable[[dict[str, Any]], None]
    payload: dict[str, Any] = field(default_factory=dict)


def idempotency_key(graph_id: str, step_name: str) -> str:
    """生成幂等键。resume 时同样的 (graph_id, step_name) 会得到同一个 key。"""
    return hashlib.sha256(f"{graph_id}:{step_name}".encode()).hexdigest()[:16]


@dataclass
class SagaResult:
    ok: bool
    completed: list[str]        # 成功完成的 step names
    compensated: list[str]      # 已补偿的 step names
    failed_step: str | None = None
    error: str | None = None


class Saga:
    """一个可重放的、支持补偿的串行编排器。

    用法::

        saga = Saga(graph_id="thread-123")
        saga.add(Step("record_ledger", do=_record, compensate=_reverse_record, payload={...}))
        saga.add(Step("notify_feishu", do=_notify, compensate=_recall_notify))
        result = saga.run(existing_log=state.get("saga_log", []))
    """

    def __init__(self, graph_id: str):
        self.graph_id = graph_id
        self.steps: list[Step] = []

    def add(self, step: Step) -> None:
        self.steps.append(step)

    def run(self, existing_log: list[dict] | None = None) -> SagaResult:
        """按顺序执行 steps；失败按逆序补偿。

        ``existing_log``：上一次运行的 saga_log，用于断点续跑。
        同 idempotency_key 状态 == "done" 的会直接跳过（幂等）。
        """
        existing_log = existing_log or []
        done_keys = {e["idempotency_key"] for e in existing_log if e.get("status") == "done"}

        completed: list[str] = []
        for step in self.steps:
            key = idempotency_key(self.graph_id, step.name)
            if key in done_keys:
                completed.append(step.name)
                continue
            try:
                step.do(step.payload)
                completed.append(step.name)
            except Exception as e:
                # 逆序补偿已成功的
                compensated = self._compensate(completed)
                return SagaResult(
                    ok=False,
                    completed=completed,
                    compensated=compensated,
                    failed_step=step.name,
                    error=repr(e),
                )

        return SagaResult(ok=True, completed=completed, compensated=[])

    def _compensate(self, completed: list[str]) -> list[str]:
        compensated: list[str] = []
        for step in reversed(self.steps):
            if step.name not in completed:
                continue
            try:
                step.compensate(step.payload)
                compensated.append(step.name)
            except Exception:
                # 补偿失败要告警，但不阻断后面的补偿
                # 生产里这里要发 PagerDuty / 飞书告警
                pass
        return compensated
