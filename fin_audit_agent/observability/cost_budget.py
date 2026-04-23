"""单请求 token 预算 + 模型路由。

**策略**：
    - 每个请求进入时分配一个 ``Budget(max_tokens, max_usd)``
    - 每次 LLM / embed 调用扣减
    - 超预算直接 raise ``BudgetExceeded``，外层节点可优雅降级

**模型路由**（成本主要靠这里省）：
    - 轻任务（意图分类、Schema Linking 排序、embedding）→ 便宜模型
    - 推理重任务（Planner、Drafter）→ 贵模型
    - 更狠：embedding 用开源 bge-m3（不花钱）
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from ..config import get_settings


class BudgetExceeded(Exception):
    """超预算触发，用于主动终止。"""


@dataclass
class Budget:
    max_tokens: int
    max_usd: float
    used_tokens: int = 0
    used_usd: float = 0.0

    def charge(self, tokens: int = 0, usd: float = 0.0) -> None:
        self.used_tokens += tokens
        self.used_usd += usd
        if self.used_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"token 超预算: {self.used_tokens}/{self.max_tokens}"
            )
        if self.used_usd > self.max_usd:
            raise BudgetExceeded(
                f"金额超预算: ${self.used_usd:.4f}/${self.max_usd:.4f}"
            )


_budget_var: ContextVar[Optional[Budget]] = ContextVar("budget", default=None)


def start_budget() -> Budget:
    """在请求入口创建预算对象并写入 ContextVar。"""
    s = get_settings()
    b = Budget(max_tokens=s.max_tokens_per_request, max_usd=s.max_usd_per_request)
    _budget_var.set(b)
    return b


def current_budget() -> Optional[Budget]:
    return _budget_var.get()


# ---------------------------------------------------------------------------
# 价格表（2025-04，粗略；生产从 Langfuse 的 price tier 读）
# ---------------------------------------------------------------------------
_PRICE_PER_1K_TOKENS = {
    "gpt-4o":         {"in": 2.50, "out": 10.00},
    "gpt-4o-mini":    {"in": 0.15, "out": 0.60},
    "deepseek-chat":  {"in": 0.14, "out": 0.28},
    "qwen-turbo":     {"in": 0.05, "out": 0.10},
}


def price_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    """估价；未知模型按 0。"""
    tier = _PRICE_PER_1K_TOKENS.get(model)
    if tier is None:
        return 0.0
    return (in_tokens * tier["in"] + out_tokens * tier["out"]) / 1000


# ---------------------------------------------------------------------------
# 模型路由
# ---------------------------------------------------------------------------
def choose_model(task_kind: str) -> str:
    """按任务类型返回该用哪个模型名。"""
    if task_kind in {"classify", "embed", "rerank", "schema_link", "ner"}:
        return get_settings().model_light
    return get_settings().model_reasoning
