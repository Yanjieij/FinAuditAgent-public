"""精排：bge-reranker-v2-m3 骨架。

**为什么 retrieval 后还要 rerank**（面试点）：
    - 粗排（BM25 / dense）追求**召回率**，召回前 top-50
    - 精排用 cross-encoder 对 (query, chunk) 建模，计算能力强但慢
    - 通常 top-50 → 精排 → top-3 送 LLM，召回 + 准确率兼顾

生产直接用 FlagEmbedding 的 bge-reranker-v2-m3；本 demo 给骨架接口，
实际跑 examples 时 rerank 就回退到粗排 top-K。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hybrid_retriever import RetrievalHit


def rerank(query: str, hits: list["RetrievalHit"], top_k: int = 3) -> list["RetrievalHit"]:
    """对 hits 做精排，返回前 top_k。

    真实实现（生产）::

        from FlagEmbedding import FlagReranker
        reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
        pairs = [(query, h.chunk.text) for h in hits]
        scores = reranker.compute_score(pairs)
        reordered = sorted(zip(hits, scores), key=lambda p: p[1], reverse=True)
        return [h for h, _ in reordered[:top_k]]

    Demo 路径：直接按粗排分数取前 top_k。
    """
    try:
        from FlagEmbedding import FlagReranker  # type: ignore

        # 真实生产路径
        reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
        pairs = [(query, h.chunk.text) for h in hits]
        scores = reranker.compute_score(pairs)
        ordered = sorted(zip(hits, scores), key=lambda p: p[1], reverse=True)
        for h, s in ordered:
            h.score = float(s)
        return [h for h, _ in ordered[:top_k]]
    except ImportError:
        # demo 回退：保留粗排顺序，取前 top_k
        return hits[:top_k]
