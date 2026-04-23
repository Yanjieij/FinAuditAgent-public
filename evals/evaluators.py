"""评测指标。

本文件尽量自包含（不强依赖 RAGAS），让任意 CI 都能跑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Score:
    name: str
    value: float
    detail: str = ""


# ---------------------------------------------------------------------------
# Text-to-SQL
# ---------------------------------------------------------------------------
def execution_match(predicted_df, gold_df) -> Score:
    """结果集比较（严格）。"""
    try:
        if predicted_df is None or gold_df is None:
            return Score("execution_match", 0.0, "at least one df is None")
        if len(predicted_df) != len(gold_df):
            return Score("execution_match", 0.0, f"row count mismatch {len(predicted_df)} vs {len(gold_df)}")
        # 列集合一致 + 值集合一致（不考虑顺序）
        pred = sorted(tuple(r) for r in predicted_df.values.tolist())
        gold = sorted(tuple(r) for r in gold_df.values.tolist())
        return Score("execution_match", 1.0 if pred == gold else 0.0)
    except Exception as e:
        return Score("execution_match", 0.0, f"error: {e}")


def syntactic_em(pred_sql: str, gold_sql: str) -> Score:
    """把空白归一化后比较 SQL 字符串。"""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip().lower())
    return Score("syntactic_em", 1.0 if norm(pred_sql) == norm(gold_sql) else 0.0)


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------
def faithfulness_lite(answer: str, evidence_chunks: list[str]) -> Score:
    """简化版 faithfulness：答案里超 20 字的数字/实体子串应当能在 evidence 里找到。

    生产用 RAGAS + LLM-as-judge。
    """
    # 抽数字 + 3+ 字的中文短语
    tokens = re.findall(r"\d[\d.,]*|[\u4e00-\u9fff]{3,}", answer)
    if not tokens:
        return Score("faithfulness_lite", 1.0, "no claims to verify")
    combined = " ".join(evidence_chunks)
    ok = sum(1 for t in tokens if t in combined)
    return Score("faithfulness_lite", ok / len(tokens),
                 detail=f"{ok}/{len(tokens)} claims found in evidence")


def citation_iou(pred_bbox: tuple[float, ...], gold_bbox: tuple[float, ...]) -> Score:
    from fin_audit_agent.rag.citation import bbox_iou
    iou = bbox_iou(pred_bbox, gold_bbox)
    return Score("citation_iou", iou, detail=f"iou={iou:.3f}")


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------
def has_evidence_pointer(text: str) -> Score:
    """答案里至少有一个 [[exec_id=...#cell=...]] 引证。"""
    has = bool(re.search(r"\[\[exec_id=[a-f0-9]+#cell=[A-Z_][A-Z0-9_]*\]\]", text))
    return Score("has_evidence_pointer", 1.0 if has else 0.0)


def has_citation(text: str) -> Score:
    has = bool(re.search(r"\[citation:[^\]]+\]", text))
    return Score("has_citation", 1.0 if has else 0.0)


def step_count(trace: list[dict]) -> Score:
    return Score("step_count", float(len(trace)))
