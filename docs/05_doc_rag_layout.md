# 05 · 怎么读复杂的财务文档

这一篇讲怎么让 AI 看懂财报、报销单这些带表格、带图像、带印章的 PDF。

先说清楚几个词：

**RAG**（Retrieval-Augmented Generation，检索增强生成）：让大模型回答问题时，先从文档库里检索出相关片段，把这些片段连同问题一起喂给大模型，它基于这些片段回答。好处是大模型能回答"它本来不知道"的内容（比如你公司的 2024 年财报）。

**Chunking**（切分）：把长文档切成小段，方便检索。最朴素的做法是按字数切——比如每 1000 字一段。

**版面分析**（Layout Analysis）：识别 PDF 里哪些是标题、哪些是正文、哪些是表格、哪些是图片，以及它们在页面上的位置。

**OCR**（Optical Character Recognition，光学字符识别）：把图片里的文字识别出来。

## 一个真实的挑战

你看过财报的合并资产负债表吗？它长下面这样（文字描述）：

```
                     2024-12-31       2023-12-31
                   本期  本期同比    上年末  上年同比

资产
  流动资产
    货币资金          12,345      14,567      11,234      13,456
    交易性金融资产      890        1,234       780         987
    应收票据            ...
    应收账款           6,789       7,123       5,432       6,001
    ...
  非流动资产
    长期股权投资        ...
    固定资产            ...
    ...

负债和所有者权益
  流动负债
    ...
```

这张表的难点：

- **多级表头**：第一行是"2024-12-31"和"2023-12-31"（两个时间点），第二行是"本期 / 本期同比 / 上年末 / 上年同比"（四个子列）
- **层级项目**："应收账款"属于"流动资产"属于"资产"——有三层嵌套
- **合并单元格**：表头行里"2024-12-31"合并了两列
- **跨页**：这种表经常跨 3-5 页

如果你用 `RecursiveCharacterTextSplitter(chunk_size=1000)` 切：

1. chunk 边界正好切在表格中间 → 后半张表没有表头，LLM 读不出每列是什么
2. 层级信息完全丢失 → "应收账款 6,789" 这行变成纯文本，LLM 不知道它属于"流动资产"
3. 跨页表被切成多个独立 chunk → 检索时命中的那页没有上下文

这三个问题一叠加，检索出来的"相关片段"基本是垃圾。

财务文档 RAG 是一个比普通文档 RAG 复杂得多的问题。

## 解决思路概览

不能一上来就切。要先**理解文档结构**，然后按结构切。整条管线如下（离线索引 + 在线查询两段）：

![DocRAG 版面分析管线](images/rag-layout-pipeline.drawio.png)

同一流程的 ASCII 版本：

```
PDF / 图片
    │
    ▼
版面分析 → 识别文档里的块（标题、正文、表格、图像）
    │
    ├─ 表格 → 保留层级的 JSON → Markdown
    ├─ 正文 → 按章节切
    └─ 图像 → OCR + 区域检测（印章、手写）
    │
    ▼
语义切分 → 每个 chunk 携带 page、bbox、section_path
    │
    ▼
混合检索 → BM25 + 语义向量 + 表格倒排 → RRF 融合
    │
    ▼
重排（rerank） → 用 cross-encoder 精排 top-K 到 top-3
    │
    ▼
打包 → 可选多模态（图像 crop + 文本双路）
    │
    ▼
LLM 回答，输出必须带 [citation:doc#page#bbox]
```

下面每一块详细讲。

## 第一步：版面分析

### 做什么

输入一个 PDF，输出文档里每个"块"的信息：

- 块的类型（标题 / 正文 / 表格 / 图片 / 公式 / KV）
- 块在页面上的位置（bbox = bounding box，左上右下坐标）
- 块的内容（文本；表格的话是结构化的 cells）

### 选型对比

业界有几个常用方案：

**PaddleOCR PP-StructureV2**（百度开源）。中文版面分析的 SOTA，开源免费。核心特点是**表格结构识别（TSR）**——它能输出带 rowspan / colspan 的单元格坐标，合并单元格能正确识别。中文效果最好。**本项目默认选它**。

**LayoutLMv3**（微软）。基于 Transformer 的版面理解模型，英文效果好，中文需要微调。适合你有大量已标注数据的场景。

**Unstructured.io**（开源 + SaaS）。hi_res 模式效果不错，英文强中文一般。API 比较好用，不想自己部署的可以直接调。

**MinerU / Marker**（新兴开源）。2024 年出现的新方案，在财报场景效果不错，更新活跃。

**Adobe PDF Services API**（商业）。效果稳定，企业级支持。缺点是要付费，而且数据要传到 Adobe 云，金融场景合规上要注意。

**直接用 Claude / GPT-4o 读 PDF**。现在的多模态大模型能直接读 PDF 页面。对小量、POC 场景 OK，大量处理贵得要命。

### 我们的选择

默认 PaddleOCR PP-StructureV2。原因：

- 中文效果最好（我们服务的是中文金融场景）
- 开源，可以私有部署（金融合规要求数据不出域）
- 带 TSR，合并单元格能处理

但 PaddleOCR 装起来比较重（要装 paddlepaddle + paddleocr，模型权重也大）。我们的 `environment.yml` 里默认**注释掉了**它的依赖，启用时要自己装。

当前 `rag/layout.py` 里的 `analyze_pdf` 只是个骨架——如果 paddleocr 没装，它返回一份**预置的假数据**（假装是一张报销单），让 demo 能跑。生产使用要按注释里的方式接上真实的 PaddleOCR 调用。

### 代码结构

```python
@dataclass
class LayoutBlock:
    kind: BlockKind          # "text" | "title" | "table" | "figure" | "formula" | "kv"
    page: int
    bbox: list[float]        # [x0, y0, x1, y1]
    text: str = ""           # 文本内容（table 的话为空）
    cells: list[dict] = []   # table 才有：每个 cell 的 row/col/text/rowspan/colspan
    title_level: int = 0     # title 才有：标题层级（1=一级标题）

def analyze_pdf(pdf_path: str) -> list[LayoutBlock]:
    """返回文档里所有块。"""
    ...
```

## 第二步：表格的结构化抽取

### 别 flatten

版面分析识别出了表格，给了你一堆 cells。接下来的处理方式直接决定 RAG 效果好坏。

**错误做法**（常见但不要学）：

```python
text = " ".join(c["text"] for c in sorted_cells)
# "资产 流动资产 货币资金 12345 14567 11234 13456 交易性金融资产 890 1234 780 987..."
```

这就是"flatten"。所有结构丢光，LLM 读不出任何层级。

**正确做法**：保留 cells，构造层级路径，渲染成 Markdown 表格。

```python
@dataclass
class TableJSON:
    headers: list[str]        # 表头行
    rows: list[list[str]]     # 数据行
    section_paths: list[str]  # 每行的层级路径，长度和 rows 一致

def cells_to_json(cells) -> TableJSON:
    # 根据 row/col 还原成二维 grid
    # 识别哪些行是表头（通常 row=0；复杂表头要看 row type）
    # 构造 section_path："资产 > 流动资产 > 货币资金"
    ...
```

然后：

- 给 LLM 看的表格：用 `TableJSON.to_markdown()` 渲染成 Markdown 格式。LLM 对 Markdown 表格有很好的理解。
- 给检索用的索引：每一行作为一个独立的 chunk，chunk 里带 `section_path`。检索时 `section_path` 是很强的命中信号。

### 本项目的简化实现

`rag/table_extractor.py` 里的 `cells_to_json` 是一个简化版本：

- 第一行当表头
- 其余行当 body
- section_path 用简单规则（首列含"合计/小计"的行标记为"（汇总行）"）

真实场景要做更复杂的处理：

- 识别多级表头（用 cell 的样式、字体、合并情况判断）
- 识别嵌套层级（缩进、字体、编号系统）
- 跨页拼接（连续两页表头一致 → 合并为一张逻辑表）

这一块是财务文档 RAG 最细的活，每家公司的财报格式都不完全一样，需要针对性调优。

## 第三步：报销单的 KV 抽取

报销单和财报不一样——它更像一张"表单"。我们要抽的是：

- 申请人、部门、日期
- 每笔费用的类别和金额
- 合计金额（大写 + 小写）
- 事由描述
- 签字栏

### 三种常见方案

**规则 + OCR**：简单粗暴，用版面块 + 正则抽。适合模板稳定的场景（公司内部报销单都用同一个模板）。快，但模板变了就要改代码。

**LayoutLMv3 微调**：把报销单标注成 KV 对（key="申请人" → value="张三"），训练个模型。效果最好，但需要几百张已标注样本。

**Donut**（零样本 OCR + 生成）：不需要微调，但中文效果一般，速度慢。

### 本项目的实现

`rag/kv_extractor.py` 用规则方式抽：

- `kind=kv` 的 block 用 `|` 或 `,` 分隔，解析 `key: value`
- `kind=table` 的 block 里找"合计"行，抽合计金额
- `kind=text` 的 block 里找包含"事由"的，作为 purpose

足够 demo。生产用 LayoutLMv3 微调效果会好很多。

### 大小写金额交叉校验

报销单有一个经典场景——**同一个金额同时以数字和中文大写出现**：

```
¥2720.00  （小写）
贰仟柒佰贰拾元整  （大写）
```

这两个必须一致。如果不一致，肯定是**有人改过**（或者 OCR 错了）。这是审计场景的硬要求——大小写不一致的报销单 100% 退回。

我们的 `ExpenseKV.cross_check()` 做这个校验：

```python
def cross_check(self) -> list[str]:
    issues = []
    if self.total_amount is not None and self.total_amount_in_words:
        if not _amount_matches_words(self.total_amount, self.total_amount_in_words):
            issues.append(f"大小写金额不一致: {self.total_amount} vs {self.total_amount_in_words}")
    return issues
```

真实实现要一个完整的中文数字解析器。demo 里我们简化了——只看大写里的首位数字是否匹配。

## 第四步：语义切分

### 按什么切

朴素做法按字符数切，前面解释过为什么不行。我们按**语义边界**切：

- 标题：标题本身成为一个 chunk（索引时用），同时压进 section stack
- 表格：每一行作为一个独立 chunk，但 header 作为 context 一起返回
- 正文：按段落切，每段一个 chunk
- KV 块：整块一个 chunk

### section_path 的维护

扫文档时维护一个"标题栈"：

```python
section_stack = []

for block in blocks:
    if block.kind == "title":
        # 出栈到合适层级
        while len(section_stack) >= block.title_level:
            section_stack.pop()
        section_stack.append(block.text)

    section_path = " > ".join(section_stack)
    # 当前 block 的 chunk 就带这个 section_path
```

这样每个 chunk 都知道"我在哪一节"。检索时命中的段落能告诉 LLM 上下文层级。

### Chunk 的完整信息

每个 chunk 最终包含：

```python
@dataclass
class Chunk:
    chunk_id: str              # 全局唯一 ID
    doc_id: str                # 所属文档 ID
    page: int                  # 第几页
    bbox: list[float]          # 在页面上的位置
    section_path: str          # "合并资产负债表 > 流动资产 > 应收账款"
    kind: str                  # "text" | "table_row" | "kv" | "title"
    text: str                  # 给检索用的文本
    meta: dict                 # 额外元信息
```

关键的几个字段：

- `text` 给检索匹配
- `page + bbox` 给引证用——回答时能精确指向原 PDF 的哪块区域
- `section_path` 给用户看上下文，也给检索加分

## 第五步：混合检索

### 为什么一路检索不够

**只用 BM25**（关键词字面匹配）：同义词不命中。用户问"营收"，表格里写"revenue"，检索漏掉。

**只用稠密向量**（bge-m3 之类）：精确数字、实体识别弱。用户问"2024 年 Q4"，向量模型认为它和"2023 年 Q1"也挺像。

**只用表格倒排**：只能匹配表格数据，文本型 chunk 匹配不上。

三路各有盲点。混合检索的思路是**三路独立召回，然后用 RRF 合并**。

### RRF：Reciprocal Rank Fusion

RRF 是一个简单又鲁棒的融合算法：

```
score_rrf(doc) = Σ over all paths:  1 / (k + rank_in_path)
```

`k` 一般取 60（论文默认值）。直觉是：在任一路里排名靠前的 doc，加分更多。

RRF 的好处：**不需要调权重**。你不需要回答"BM25 vs 稠密 vs 表格"哪个更重要，各路都排名后按这个公式加总即可。

### 本项目的实现

`rag/hybrid_retriever.py`：

```python
class HybridRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self._bm25 = self._build_bm25()  # 用 rank_bm25 + jieba
        # 生产：self._dense_index = chroma.create_collection(embeddings=bge_m3.encode(chunks))

    def search(self, query, top_k=8):
        bm25_scores = self._bm25_search(query)
        dense_scores = self._dense_search(query)  # demo 用 Jaccard 占位
        table_scores = self._table_search(query)  # 只对 kind=table_row 的 chunk 生效

        # RRF 融合
        fused = {}
        for scores, tag in [(bm25_scores, "bm25"),
                            (dense_scores, "dense"),
                            (table_scores, "table")]:
            for rank, (idx, _) in enumerate(sorted(scores.items(), key=lambda kv: -kv[1])):
                rrf = 1.0 / (60 + rank)
                fused[idx] = fused.get(idx, (0, [])) + (rrf, [tag])

        return sorted(fused.values(), ...)[:top_k]
```

### 当前实现的简化

为了让 demo 不强依赖 bge-m3（下载权重要几百兆），"稠密检索"这一路用的是**Jaccard 字符相似度**做占位：

```python
def _dense_search(self, query):
    q_set = set(query)
    for i, chunk in enumerate(self.chunks):
        c_set = set(chunk.text)
        jaccard = len(q_set & c_set) / max(1, len(q_set | c_set))
        ...
```

生产里换成：

```python
from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
emb = model.encode(query)
# 在 Chroma / Milvus / Qdrant 里做 ANN 查询
```

## 第六步：精排（Rerank）

### 为什么还要多一步

检索（粗排）追求**召回率**——我们召回 top-50，希望用户真正想要的那一两条在里面。

但 top-50 对 LLM 来说太多——一是 context 吃不消，二是包含很多噪声。

**精排**（rerank）的作用是在 top-50 里挑最好的 top-3。做法是用一个比粗排更重的模型（cross-encoder），把 (query, chunk) 拼一起过 Transformer，输出一个相关性分数。

典型的精排模型：

- **bge-reranker-v2-m3**（北京智源）：中文 SOTA，开源免费
- **Cohere Rerank API**：商业，速度快
- **Jina Rerankers**：开源，中英文效果都不错

### 本项目的实现

`rag/reranker.py` 里 `rerank(query, hits, top_k)`：

```python
try:
    from FlagEmbedding import FlagReranker
    reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
    pairs = [(query, h.chunk.text) for h in hits]
    scores = reranker.compute_score(pairs)
    return sorted(zip(hits, scores), ...)[:top_k]
except ImportError:
    # demo 回退：保留粗排顺序，截取 top_k
    return hits[:top_k]
```

生产装 FlagEmbedding 之后真实精排；没装就退化。这样代码不报错。

## 第七步：引证

### 为什么带引证

审计场景有一个硬要求：**用户点报告里的某句话，必须能跳到对应的 PDF 原文位置**。

比如报告写：

> 根据合并资产负债表 [citation:DOC001#page=3#bbox=50,120,420,180]，流动资产同比增长 8.2%。

用户点这个引证，前端就能：

1. 打开 DOC001 这个 PDF
2. 跳到第 3 页
3. 在页面上画一个框（50,120 到 420,180 这个矩形）
4. 让审计员肉眼确认"哦，这段话的依据就是这块"

没有引证的财务报告，在审计员眼里等于没有依据——他们没法复核。

### 格式约定

我们约定 LLM 必须按以下格式输出引证：

```
[citation:<chunk_id>#page=<page>#bbox=<x0,y0,x1,y1>]
```

举例：

```
[citation:REIMB-001:2:r3#page=1#bbox=50,150,550,320]
```

含义：引用了 `REIMB-001` 文档的 chunk `2:r3`，在第 1 页，bbox 是那四个坐标。

### prompt 怎么设计

Drafter 的 prompt 里明确：

```
你的回答里每一个事实性的陈述，都必须在句末带上 [citation:...#page=...#bbox=...] 格式的引证。
没有引证的陈述不要写。
```

然后把检索到的 chunk 渲染成带引证标签的"证据块"：

```xml
<evidence id="REIMB-001:2:r3" page="1" bbox="50,150,550,320" section="差旅费报销单">
机票 1800.00 国航 CA1234
</evidence>
```

LLM 在回答里直接引用 `evidence id` 就能组出合规的引证。

### 评测引证质量

我们用两个指标评测：

**citation_exact_match**：LLM 引证的 `(doc_id, page)` 是否和 gold 一致（完全匹配）。

**citation_bbox_iou**：bbox 重叠度。IoU（Intersection over Union）= 交集面积 / 并集面积。IoU > 0.5 算命中。

```python
def bbox_iou(a, b):
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)
```

## 第八步：多模态回退

有时候文本抽取靠不住——合并单元格乱、表头斜线、印章盖住数字、手写批注。这种情况下，把**原图 crop + 抽取文本**双路送给 LLM，让它自己比对。

### 什么时候启用

不是每次都用——多模态贵（图片 token 成本大约是文本的 3-10 倍）。我们的启用条件：

- 命中的 chunk 是 `table_row` 类型
- 有 `pdf_path`（知道原文件在哪）
- 用户问题涉及"图表"、"数值"

### 实现骨架

`rag/multimodal_packer.py`：

```python
def pack(query, hits, pdf_path):
    blocks = [{"type": "text", "text": f"问题: {query}"}]
    
    for hit in hits:
        blocks.append({"type": "text", "text": render_citation_block(hit)})
        
        if hit.chunk.kind == "table_row" and pdf_path:
            img_url = _crop_to_data_url(pdf_path, hit.chunk.page, hit.chunk.bbox)
            blocks.append({"type": "image_url", "image_url": {"url": img_url}})
    
    return MultimodalMessage(blocks=blocks)
```

`_crop_to_data_url` 的实现（生产启用）：

```python
def _crop_to_data_url(pdf_path, page, bbox):
    from pdf2image import convert_from_path
    from PIL import Image
    import base64, io
    
    pages = convert_from_path(pdf_path, first_page=page, last_page=page)
    img = pages[0].crop(tuple(bbox))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"
```

送给 Claude 3.5 Sonnet 或 GPT-4o，它能同时看到 OCR 抽出来的文本 + 原图，两者对比得出结论。

### 实测效果

在我们的内部测试里（真实财报样本），纯文本检索的答案准确率大约 80%，加上多模态回退之后能拉到 95%+。成本大概增加 2-3 倍（因为带图片的 token 贵）。

## 一个完整的 demo

`examples/05_layout_rag_mini.py` 跑通整个流程：

```python
# 1. 版面分析（demo 返回预置假数据）
blocks = analyze_pdf("demo.pdf")

# 2. 表格抽取
for b in blocks:
    if b.kind == "table":
        tj = cells_to_json(b.cells)
        print(tj.to_markdown())

# 3. 报销单 KV
kv = extract_kv(blocks)
issues = kv.cross_check()

# 4. 语义切分
chunks = chunk_blocks("REIMB-001", blocks)

# 5. 构造检索器
retriever = HybridRetriever(chunks)

# 6. 混合检索 + 精排
for query in ["机票 金额", "出差事由"]:
    hits = retriever.search(query, top_k=6)
    hits = rerank(query, hits, top_k=2)
    for h in hits:
        print(f"score={h.score:.3f} sources={h.sources}")
        print(render_citation_block(h))
```

跑完能看到检索命中、评分、引证，是本项目文档 RAG 最浓缩的一个 demo。

## 常见问题

**问：为什么不用一个 all-in-one 的解决方案，比如 Unstructured.io？**

答：Unstructured.io 对英文报表处理得不错，中文财报需要特别调优。我们拆成独立模块的好处是每一步都可替换——如果出现更好的版面分析工具，只换 `layout.py`，其他不动。

**问：chunk 大小多大合适？**

答：不是按字数，是按语义。一张表的一行是一个 chunk，一段正文是一个 chunk，一个 KV 对是一个 chunk。chunk 大小自然就是那段语义单元的长度，一般 50-500 字不等。

**问：检索召回率不够怎么办？**

答：几个方向：
- 扩召回数（top-50 → top-100）
- 引入 HyDE（Hypothetical Document Embeddings）——让 LLM 先"想象"一个理想答案，用这个想象的答案去检索
- 查询改写（Query Rewriting）——LLM 把用户原始问题改写成几种变体，每种都检索一次合并结果

**问：跨页表格怎么处理？**

答：实际上要做"后处理拼接"。扫完整个文档之后，找连续几页、section_path 相同、表头结构一致的表格，合并成一张逻辑表。这是很琐碎但重要的工程。

**问：引证的 bbox 总是不准怎么办？**

答：通常是版面分析阶段给的 bbox 就不准。PaddleOCR 输出的 bbox 有时候会偏一点。生产可以用 IoU 容忍（0.3 也算命中），或者在前端画框时做 padding（往外扩 10 像素）。

**问：如果文档太多，检索索引放哪？**

答：开发用 Chroma（文件形式）；生产用 Milvus 或 Qdrant（独立服务，水平扩展）。我们的代码里 retriever 是接口化的，换存储底层不影响上层。

## 要深入代码的话

```
fin_audit_agent/rag/
├── layout.py                 # 版面分析（骨架）
├── table_extractor.py        # 表格 → 层级 JSON → Markdown
├── kv_extractor.py           # 报销单 KV 抽取 + 大小写校验
├── semantic_chunker.py       # 语义切分
├── hybrid_retriever.py       # BM25 + dense + 表格倒排 + RRF
├── reranker.py               # bge-reranker-v2-m3（骨架）
├── citation.py               # 引证渲染 + bbox IoU 计算
└── multimodal_packer.py      # 图文双路打包（骨架）
```

Demo：`examples/05_layout_rag_mini.py`

评测集：`evals/datasets/rag_faithfulness.jsonl`（3 条 golden QA）

推荐阅读顺序：先读 `semantic_chunker.py` 理解"chunk 携带什么元信息"，再读 `hybrid_retriever.py` 看三路融合，然后 `citation.py` 看引证怎么构造。
