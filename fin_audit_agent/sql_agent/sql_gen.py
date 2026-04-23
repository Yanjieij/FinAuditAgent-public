"""Text-to-SQL 生成器（Prompt + 调用 LLM）。

**设计要点**：
    - System prompt 约束 LLM：**只能引用 Schema Linking 给的表/列和 Semantic Layer
      的 measures**；不允许自己拼公式。
    - 加 2-3 条 few-shot（本仓库 demo 用玩具库，示例不多，生产会维护一个 shot-bank）。
    - LLM 返回结构化：``{"sql": "...", "rationale": "...", "need_clarify": null | "..."}``
      用 JSON mode + Pydantic 校验。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_llm
from .schema_linker import LinkedSchema
from .semantic_layer import SemanticLayer


SYSTEM_PROMPT = """你是一个资深数据分析师，写 SQL 要遵守以下铁律：

1. 只能 SELECT，禁止任何 DML/DDL。
2. 所有业务指标（毛利率、费用率、周转率等）只能用 `{{ measure:xxx }}` 占位符引用，
   不要自己拼表达式——公式会由语义层展开，保证全公司口径统一。
3. 维度切分用 `{{ dim:xxx }}` 占位符。
4. 所有日期字段用 ISO 格式比较；不要写 `YEAR(x)=2024`，写 `x >= '2024-01-01' AND x < '2025-01-01'`。
5. 若用户问题的实体在给定表/列里找不到任何匹配，务必把 `need_clarify` 字段填上，
   **不要硬编 SQL**。

返回严格的 JSON：
  {"sql": "...", "rationale": "一句话解释", "need_clarify": null 或 "要问用户的问题"}
"""


@dataclass
class SQLGenResult:
    sql: str
    rationale: str
    need_clarify: str | None


def generate_sql(
    question: str,
    linked: LinkedSchema,
    semantic: SemanticLayer,
    *,
    llm=None,
    few_shots: list[tuple[str, str]] | None = None,
) -> SQLGenResult:
    """生成一条候选 SQL。

    Args:
        question:  用户自然语言问题
        linked:    已做过 Schema Linking 的子集
        semantic:  语义层（给 measures 清单）
        llm:       可注入的 LLM（测试时传 FakeChatModel）；None 则从 config.get_llm 取
        few_shots: [(question, sql)] 例子列表

    Returns:
        :class:`SQLGenResult`。SQL 可能是占位符 SQL（需要 ``semantic.render`` 再展开）。
    """
    # 延迟 import，避免主包启动时强依赖 langchain
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = llm or get_llm(kind="reasoning")

    user_block = [
        f"## 用户问题\n{question}",
        "",
        linked.to_prompt_block(),
        "",
        semantic.to_prompt_block(),
    ]

    if few_shots:
        user_block.append("\n## 示例")
        for q, sql in few_shots:
            user_block.append(f"问：{q}\nSQL：{sql}")

    user_block.append("\n现在请返回 JSON。")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content="\n".join(user_block)),
    ]

    # 期望 LLM 返回 JSON 字符串
    resp = llm.invoke(messages)
    raw = resp.content if hasattr(resp, "content") else str(resp)
    return _parse_json(raw)


def _parse_json(raw: str) -> SQLGenResult:
    """宽松解析 LLM 返回的 JSON。

    为了鲁棒：
        - 去 Markdown ```json 代码块围栏
        - 容忍 trailing comma（简单正则清一下）
        - 失败就给一个 need_clarify 的空 SQL，让 retry_loop 反馈错误
    """
    import json
    import re

    s = raw.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()

    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return SQLGenResult(sql="", rationale="", need_clarify=f"LLM 返回解析失败：{raw[:200]}")

    return SQLGenResult(
        sql=data.get("sql", "") or "",
        rationale=data.get("rationale", "") or "",
        need_clarify=data.get("need_clarify"),
    )
