"""RAG 工具：LLM 触发文档检索。
"""

from __future__ import annotations

import json

from ..auth.audit_log import AuditLog, hash_text
from ..auth.injection_guard import wrap_untrusted
from ..auth.token_context import current_user, require_scope
from ..rag.citation import render_citation_block
from ..rag.hybrid_retriever import HybridRetriever
from ..rag.reranker import rerank
from ..rag.semantic_chunker import Chunk


def search_docs(
    query: str,
    retriever: HybridRetriever,
    *,
    top_k: int = 3,
) -> str:
    """检索文档并返回带引证的证据块。

    Args:
        query:     自然语言查询
        retriever: 预构建的 HybridRetriever（在 graph 初始化或 CLI 启动时构建好）
        top_k:     rerank 后保留的 chunk 数

    **权限**：需 ``read:documents`` scope。
    """
    require_scope("read:documents")
    user = current_user()

    hits = retriever.search(query, top_k=max(top_k * 3, 8))  # 粗排多召回
    hits = rerank(query, hits, top_k=top_k)

    payload = {
        "query": query,
        "n_hits": len(hits),
        "evidence": [render_citation_block(h) for h in hits],
    }

    try:
        AuditLog().append(
            user=user,
            action="rag.search",
            input_hash=hash_text(query),
            output_hash=hash_text(str(payload)[:500]),
            metadata={"n_hits": len(hits)},
        )
    except Exception:
        pass

    return wrap_untrusted(json.dumps(payload, ensure_ascii=False), source="rag")
