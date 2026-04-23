"""模型工厂与全局配置。

设计要点（面试常问）：
    1. **为什么用 OpenAI 兼容 API 而不是 Anthropic/Google 原生 SDK？**
       因为国内金融场景，DeepSeek / 通义 / 自建 vLLM 都暴露 OpenAI 兼容端点，
       统一一层调用可以在不改业务代码的前提下切模型，做 A/B 或成本路由。

    2. **为什么要有 reasoning / light 两套模型？**
       成本路由：embed、意图分类、Schema Linking 这些轻任务用便宜模型（gpt-4o-mini
       / deepseek-chat）；只有 Planner、Drafter 这类推理任务用贵模型。这是 Langfuse
       trace 里把 token 成本打下来的关键动作，面试可以直接报数字。

    3. **temperature=0 的选择**：
       Agent 场景下 **不想要多样性**，要的是可复现。任何需要"创意"的节点都应该在
       提示词里约束而非用高温。

用法：

    from fin_audit_agent.config import get_llm
    llm = get_llm()                    # 默认推理模型
    llm_light = get_llm(kind="light")  # 轻量路由
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv

# 启动时加载 .env；幂等，重复调用无副作用
load_dotenv()


# ---------------------------------------------------------------------------
# 全局配置 dataclass：集中管理所有环境变量读取，避免散落在各模块里
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """从 .env 读取的全局配置。用 frozen dataclass 防止运行时被改写。"""

    # ==== LLM ====
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_reasoning: str = os.getenv("FIN_LLM_MODEL_REASONING", "gpt-4o")
    model_light: str = os.getenv("FIN_LLM_MODEL_LIGHT", "gpt-4o-mini")

    # ==== Langfuse ====
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    langfuse_host: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    # ==== DB ====
    db_url: str = os.getenv("FIN_DB_URL", "sqlite:///./.fin_audit.db")

    # ==== 安全密钥 ====
    hitl_hmac_secret: str = os.getenv("FIN_HITL_HMAC_SECRET", "dev-only-secret")
    audit_hmac_secret: str = os.getenv("FIN_AUDIT_HMAC_SECRET", "dev-only-secret")

    # ==== 沙箱 ====
    sandbox_timeout_sec: int = int(os.getenv("FIN_SANDBOX_TIMEOUT_SEC", "10"))
    sandbox_mem_mb: int = int(os.getenv("FIN_SANDBOX_MEM_MB", "512"))
    e2b_api_key: str = os.getenv("E2B_API_KEY", "")

    # ==== 成本 ====
    max_tokens_per_request: int = int(os.getenv("FIN_MAX_TOKENS_PER_REQUEST", "100000"))
    max_usd_per_request: float = float(os.getenv("FIN_MAX_USD_PER_REQUEST", "0.50"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例 Settings。用 lru_cache 保证只初始化一次。"""
    return Settings()


# ---------------------------------------------------------------------------
# LLM 工厂
# ---------------------------------------------------------------------------
ModelKind = Literal["reasoning", "light"]


def get_llm(kind: ModelKind = "reasoning", temperature: float = 0.0, **kwargs):
    """返回一个 ChatOpenAI 实例。

    Args:
        kind: "reasoning"（贵、强）或 "light"（便宜、快，用于 embed/classify）。
        temperature: 默认 0，保证可复现。
        **kwargs: 透传给 ``ChatOpenAI`` 的其他参数，例如 ``max_tokens`` / ``streaming``。

    为什么用延迟 import：
        ``langchain_openai`` 启动时会做一些校验，如果用户暂时不跑 LLM 相关代码（比如
        只跑 ``sandbox/number_verifier.py`` 的单元测试），不应该强制它装 langchain。
    """
    from langchain_openai import ChatOpenAI  # 延迟 import

    settings = get_settings()
    model_name = settings.model_reasoning if kind == "reasoning" else settings.model_light

    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=temperature,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 一些语义化的常量（避免 magic number）
# ---------------------------------------------------------------------------
class Limits:
    """到处散着用的魔法数字，集中到这里，方便 grep 和调参。"""

    # 痛点 2：Text-to-SQL 最大重试次数
    SQL_MAX_RETRIES: int = 3
    # 痛点 2：SQL 自动注入的 LIMIT
    SQL_DEFAULT_LIMIT: int = 10_000
    # 痛点 4：Analyze 节点 ReAct bounded iterations
    REACT_MAX_ITERATIONS: int = 5
    # 痛点 4：HITL 超时自动拒绝（秒）
    HITL_TIMEOUT_SEC: int = 24 * 3600
    # 痛点 5：retrieval top-K
    RAG_TOP_K: int = 8
    # 痛点 5：rerank 后保留数
    RAG_RERANK_K: int = 3
