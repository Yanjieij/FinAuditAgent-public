"""痛点 5：复杂财务文档 RAG（版面分析 + 表格层级保留 + 多模态回退）。

**为什么财务文档 RAG 特殊**：
    - 合并资产负债表：**多级表头 + 合并单元格**，RecursiveCharacterTextSplitter 会彻底切碎
    - 报销单：**图像 + 手写金额 + 印章 + 多区域 KV**，需要版面分析
    - 财报 PDF：**跨页表**，chunk 按字符切会把 12 月数据塞进第 5 页的 chunk 里

**流水线**（本包）::

    PDF/Image ── layout.py ──► 版面元素（文本块 / 表格 / 图片 / 标题）
                                   │
                  ┌────────────────┼───────────────────┐
                  ▼                ▼                   ▼
            table_extractor   semantic_chunker    kv_extractor (报销单)
            (层级 JSON →       (按 section+表格     (LayoutLMv3 / Donut)
             Markdown)          单元切)
                  │                │                   │
                  └────────────────┼───────────────────┘
                                   ▼
                           hybrid_retriever
                         (BM25 + bge-m3 + 表格倒排 → RRF)
                                   │
                                   ▼
                              reranker (bge-reranker-v2-m3)
                                   │
                                   ▼
                        citation (chunk → page/bbox/img_crop)
                                   │
                                   ▼
                     multimodal_packer (图像 + 文本双路送 LLM)
"""
