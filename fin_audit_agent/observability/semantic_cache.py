"""语义缓存（Redis + embedding 相似度）——骨架。

**场景**：
    用户反复问"Q4 销售费用率"，每次都跑一遍 SQL + 沙箱算太浪费。若问题的 embedding
    和历史某次相似度 > 0.95，直接返回缓存的答案 / 数字。

**设计**：
    - Key：query 的 bge-m3 embedding（向量），value：上次完整的 final_answer
    - Redis 7 的 Vector Search 可以直接做 ANN
    - TTL 设 1 小时（财务数据日更，超过 1 小时可能失效）

**警惕**（面试陷阱）：
    - 不能无脑命中：用户可能上下文变了（权限/租户），缓存要**按用户+租户分 key**
    - 金融写操作绝不能缓存（如审批结果）——只缓存查询类
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CacheEntry:
    query: str
    answer: str
    ts_ms: int
    user_tenant: str


class SemanticCache:
    """骨架：给一个接口轮廓，生产填 Redis/Chroma。"""

    def get(self, query: str, user_tenant: str, threshold: float = 0.95) -> Optional[CacheEntry]:
        # 生产实现（伪代码）：
        #   emb = bge_m3.encode(query)
        #   hits = redis.ft(index).search(f"@user_tenant:{{{user_tenant}}}",
        #                                  query_vector=emb, top_k=1)
        #   if hits and hits[0].score > threshold: return hits[0]
        return None

    def put(self, query: str, answer: str, user_tenant: str) -> None:
        # 生产实现：存 embedding + metadata + TTL
        pass
