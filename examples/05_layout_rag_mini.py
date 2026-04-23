"""Example 05 —— 版面分析 + 表格层级保留 + 多模态 RAG。

演示：
    1. 用 ``layout.analyze_pdf``（demo skeleton）拿到版面 blocks（含表格）
    2. ``kv_extractor.extract`` 抽报销单 KV + 大小写金额交叉校验
    3. ``semantic_chunker`` 按章节/表格单元切
    4. ``hybrid_retriever`` 混合检索 + 引证
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from fin_audit_agent.rag.citation import render_citation_block
from fin_audit_agent.rag.hybrid_retriever import HybridRetriever
from fin_audit_agent.rag.kv_extractor import extract as extract_kv
from fin_audit_agent.rag.layout import analyze_pdf
from fin_audit_agent.rag.reranker import rerank
from fin_audit_agent.rag.semantic_chunker import chunk_blocks
from fin_audit_agent.rag.table_extractor import cells_to_json

console = Console()


def main() -> None:
    console.rule("[bold]1. 版面分析（demo 假数据）[/bold]")
    blocks = analyze_pdf("demo.pdf")  # skeleton 返回一个假报销单 layout
    for b in blocks:
        console.print(f"  {b.kind}@p{b.page} {b.bbox} :: {b.text[:60] if b.text else f'{len(b.cells)} cells'}")

    console.rule("[bold]2. 表格转层级 JSON + Markdown[/bold]")
    table_block = next(b for b in blocks if b.kind == "table")
    tj = cells_to_json(table_block.cells)
    console.print("Markdown 渲染：")
    console.print(tj.to_markdown())

    console.rule("[bold]3. KV 抽取 + 大小写金额交叉校验[/bold]")
    kv = extract_kv(blocks)
    tbl = Table()
    tbl.add_column("field"); tbl.add_column("value")
    for k, v in kv.__dict__.items():
        tbl.add_row(k, str(v))
    console.print(tbl)
    issues = kv.cross_check()
    console.print(f"交叉校验问题: {issues or '无'}")

    console.rule("[bold]4. 语义切分 → 混合检索 → 引证[/bold]")
    chunks = chunk_blocks("REIMB-001", blocks)
    retriever = HybridRetriever(chunks)

    for query in ["机票 金额", "出差事由"]:
        hits = retriever.search(query, top_k=6)
        hits = rerank(query, hits, top_k=2)
        console.print(f"\nQ: [cyan]{query}[/cyan]")
        for h in hits:
            console.print(f"  score={h.score:.3f} src={h.sources}  chunk={h.chunk.text[:80]!r}")
            console.print(render_citation_block(h))


if __name__ == "__main__":
    main()
