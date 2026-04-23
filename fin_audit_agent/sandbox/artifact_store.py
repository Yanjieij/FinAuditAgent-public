"""沙箱产物存储。

每次沙箱执行会：
    1. 分配一个 UUID 作为 ``exec_id``
    2. 在 ``.artifacts/<exec_id>/`` 下落盘 DataFrame（parquet）、图表（png）等
    3. 返回结构化的 :class:`ExecResult`，里面引用这些路径

为什么要落盘（而不是把 DataFrame JSON 序列化后塞 LLM context）：
    - 财务表通常几千几万行，塞进去等于自杀
    - Drafter 只需要 summary + 几个关键 cell；要看详情时再按 exec_id 拉
    - 产物落盘后可以配 S3 归档，审计时可回放
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .result_schema import Artifact

# 根目录：相对项目根，.gitignore 里已加
_ARTIFACT_ROOT = Path(".artifacts")


def new_exec_id() -> str:
    """生成一个新的 exec_id（UUID4 前 8 位就够了，够 16M 空间不撞）。"""
    return uuid.uuid4().hex[:12]


def artifact_dir(exec_id: str) -> Path:
    """返回该 exec_id 的产物目录，不存在则建。"""
    d = _ARTIFACT_ROOT / exec_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_dataframe(exec_id: str, name: str, df: Any) -> Artifact:
    """把 pandas DataFrame 存成 parquet，返回 Artifact。

    注意：``preview`` 只取前 5 行，避免 LLM context 爆炸。
    """
    path = artifact_dir(exec_id) / f"{name}.parquet"
    # pandas 是可选依赖，这里延迟 import 避免 sandbox/ 整个包对 pandas 强依赖
    df.to_parquet(path, index=False)
    return Artifact(
        kind="table",
        path=str(path),
        description=f"DataFrame '{name}' rows={len(df)} cols={list(df.columns)}",
        preview=df.head(5).to_dict("records"),
    )


def save_chart(exec_id: str, name: str, fig: Any) -> Artifact:
    """把 matplotlib Figure 存成 png。"""
    path = artifact_dir(exec_id) / f"{name}.png"
    fig.savefig(path, dpi=100, bbox_inches="tight")
    return Artifact(
        kind="chart",
        path=str(path),
        description=f"Chart '{name}'",
        preview=None,  # 缩略图可以之后再加
    )


def save_json_cells(exec_id: str, cells: dict[str, Any]) -> None:
    """把"命名变量"导出的 cell dict 落盘。用于事后审计溯源。"""
    (artifact_dir(exec_id) / "cells.json").write_text(
        json.dumps(cells, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
