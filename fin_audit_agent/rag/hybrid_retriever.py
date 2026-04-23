"""混合检索：BM25 + dense embedding + 表格单元倒排，RRF 融合。

**为什么混合检索**（面试点）：

    - **BM25**：字面命中强；中文场景用 jieba 分词
    - **Dense（bge-m3）**：语义理解强；处理同义替换（"营收" ↔ "revenue"）
    - **表格单元倒排**：对表格行的数字/单元格专门建索引，RAG 提问"XX 公司 Q4 营收是多少"时直达

    单路检索都有盲点，RRF（Reciprocal Rank Fusion）无需调权，简单鲁棒。

**本 demo 实现**：
    - BM25 用 ``rank_bm25``（jieba 分词）
    - Dense 为了避免强依赖模型权重，**用简化的 TF-IDF 余弦**当占位；
      生产替换为 bge-m3 + Chroma
    - RRF 融合 top-K
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .semantic_chunker import Chunk


@dataclass
class RetrievalHit:
    chunk: Chunk
    score: float
    sources: list[str]  # 哪几路命中的："bm25" / "dense" / "table"


class HybridRetriever:
    """一个小而完整的混合检索器。"""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._bm25 = self._build_bm25()
        # dense 的真实实现：self._embeddings = [bge_m3.encode(c.text) for c in chunks]
        # demo 跳过，直接在 query 时计算 Jaccard 作语义近似

    def _build_bm25(self):
        try:
            import jieba
            from rank_bm25 import BM25Okapi
        except ImportError:
            return None

        corpus = [[w for w in jieba.cut(c.text) if w.strip()] for c in self.chunks]
        return BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int = 8) -> list[RetrievalHit]:
        bm25_scores = self._bm25_search(query) if self._bm25 else {}
        dense_scores = self._dense_search(query)
        table_scores = self._table_search(query)

        # 三路 RRF 融合
        fused: dict[str, tuple[float, list[str]]] = {}
        for rank_map, tag in [(bm25_scores, "bm25"),
                              (dense_scores, "dense"),
                              (table_scores, "table")]:
            if not rank_map:
                continue
            # 按分数排序，拿到 rank
            ordered = sorted(rank_map.items(), key=lambda kv: kv[1], reverse=True)
            for rank, (idx, _) in enumerate(ordered):
                rrf = 1.0 / (60 + rank)  # RRF k=60 是论文常用
                cur = fused.get(idx, (0.0, []))
                fused[idx] = (cur[0] + rrf, cur[1] + [tag])

        ranked = sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
        return [
            RetrievalHit(chunk=self.chunks[i], score=score, sources=src)
            for i, (score, src) in ranked
        ]

    # ---------- 各路实现 ----------
    def _bm25_search(self, query: str) -> dict[int, float]:
        import jieba

        toks = [w for w in jieba.cut(query) if w.strip()]
        scores = self._bm25.get_scores(toks)
        return {i: float(s) for i, s in enumerate(scores) if s > 0}

    def _dense_search(self, query: str) -> dict[int, float]:
        """占位：Jaccard 字符集相似度。生产改为 bge-m3 余弦。"""
        q_set = set(query)
        out: dict[int, float] = {}
        for i, c in enumerate(self.chunks):
            c_set = set(c.text)
            inter = len(q_set & c_set)
            union = len(q_set | c_set)
            if union > 0:
                out[i] = inter / union
        return out

    def _table_search(self, query: str) -> dict[int, float]:
        """表格单元专项：只在 kind='table_row' 的 chunk 里做命中。"""
        out: dict[int, float] = {}
        for i, c in enumerate(self.chunks):
            if c.kind != "table_row":
                continue
            if any(tok in c.text for tok in query.split()):
                out[i] = 1.0
        return out
