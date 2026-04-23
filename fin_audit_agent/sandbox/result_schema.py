"""沙箱执行结果的标准结构。

面试讲点：
    Agent 与工具的接口必须是**结构化的**，不是自由文本。
    一旦结构化，Drafter 节点就能用 ``exec_id`` 做数字回填，从而实现 "evidence-pointer
    输出契约"（数字不在 LLM 生成里凭空出现，而是引用沙箱某次执行的某个 cell）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Artifact:
    """沙箱产物：一张表、一张图、或任意文件。"""

    kind: str  # "table" | "chart" | "file"
    path: str  # 相对 artifact_store 根目录的相对路径
    description: str = ""
    preview: Any = None  # 前几行 / 缩略图 base64 / 数值摘要


@dataclass
class ExecResult:
    """单次沙箱执行的完整返回。

    Attributes:
        exec_id:    全局唯一 ID（UUID），Drafter 用它做数字引证
        ok:         是否成功执行（False 时看 error）
        stdout:     标准输出截断到 ~8KB，避免撑爆 LLM context
        stderr:     标准错误，同样截断
        wall_time_ms:  墙钟时间，用于成本/慢请求分析
        artifacts:  执行产出的文件清单（DataFrame 存 parquet，图存 png）
        cells:      结构化的变量导出，例如 ``{"revenue_q4": 12345.67}``
                    Drafter 的 evidence-pointer 引用就是指向 cell 名
        error:      失败时的异常字符串
    """

    exec_id: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    wall_time_ms: float = 0.0
    artifacts: list[Artifact] = field(default_factory=list)
    cells: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def summary(self) -> str:
        """给 LLM 看的一行摘要；完整内容在 artifacts 里。"""
        if not self.ok:
            return f"[exec_id={self.exec_id}] FAILED: {self.error}"
        cells_preview = ", ".join(f"{k}={v!r}" for k, v in list(self.cells.items())[:5])
        return (
            f"[exec_id={self.exec_id}] OK "
            f"wall={self.wall_time_ms:.0f}ms "
            f"cells={{{cells_preview}}} "
            f"artifacts={len(self.artifacts)}"
        )
