"""生产级沙箱：e2b 云沙箱适配（骨架）。

**为什么生产要换掉 RestrictedPython？**

    RestrictedPython 是**进程内编译期改写**，没有资源隔离（OOM 能把主进程拖挂）、
    没有 seccomp syscall 拦截、没有文件系统 chroot。一旦 LLM 生成的代码利用了
    RestrictedPython 未知的绕过漏洞，Agent 所在的整个 worker 就被攻破。

**为什么选 e2b 而不是自建 Docker**：

    - 冷启 300-500ms，比 Docker 快 2-3 倍；独立 MicroVM 级隔离
    - 多语言 SDK 一致（Python/Node/Go 都能嵌）
    - 自动文件/产物管理，免去自己写 artifact 上传逻辑
    - 按秒计费，成本可控
    - **缺点**：出网费用高，国内访问需要走代理

**什么时候自建 nsjail + seccomp（面试进阶）**：

    - 合规要求数据不出网（金融场景常见）
    - 单机并发要求高（每秒 100+ 次沙箱执行）
    - 有专职 SRE 维护镜像 + seccomp profile

---

当前文件只给**接口骨架 + 调用示例**，真实生产用时：
    1. `pip install e2b` 并填 E2B_API_KEY
    2. 把本文件的 TODO 实现补全
    3. 在 :mod:`fin_audit_agent.tools.sandbox_tool` 里把 `run_code` 的 import 切到这里
"""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from .artifact_store import new_exec_id
from .result_schema import ExecResult


def run_code_e2b(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout_sec: int = 10,
) -> ExecResult:
    """在 e2b 云沙箱里执行代码。

    **未实现**：本函数目前是骨架，抛 ``NotImplementedError``。生产启用时按下面的模板实现：

    .. code-block:: python

        from e2b_code_interpreter import Sandbox

        with Sandbox(timeout=timeout_sec) as sbx:
            # 注入输入变量：可以把 DataFrame 序列化上传 sbx.files.write(...)
            execution = sbx.run_code(code)
            # 处理 stdout / stderr / results（其中有图表）
            # 下载产物到本地 artifact_store

    Raises:
        NotImplementedError: 默认抛。生产使用时实现上述 TODO。
    """
    settings = get_settings()
    if not settings.e2b_api_key:
        # 对外明示原因，避免误以为代码 bug
        raise NotImplementedError(
            "e2b_runner 未启用。要启用：\n"
            "  1. pip install e2b-code-interpreter\n"
            "  2. .env 填 E2B_API_KEY\n"
            "  3. 实现本函数（见 docstring 模板）\n"
            "目前请使用 sandbox.runner.run_code（本地 RestrictedPython 路径）"
        )
    raise NotImplementedError("TODO: 按 docstring 模板接入 e2b_code_interpreter")


# ---------------------------------------------------------------------------
# 方案对照表（供 docs/01_sandbox_guide.md 引用）
# ---------------------------------------------------------------------------
SANDBOX_COMPARISON = {
    "RestrictedPython": {
        "isolation": "进程内（弱）",
        "cold_start_ms": 0,
        "oom_safe": False,
        "network_iso": False,
        "适合": "本地 demo、面试讲编译期防御原理",
    },
    "e2b": {
        "isolation": "MicroVM（强）",
        "cold_start_ms": 400,
        "oom_safe": True,
        "network_iso": True,
        "适合": "出海业务、SaaS 场景",
    },
    "Docker+gVisor": {
        "isolation": "用户态内核（强）",
        "cold_start_ms": 800,
        "oom_safe": True,
        "network_iso": True,
        "适合": "自建 K8s 集群",
    },
    "nsjail+seccomp": {
        "isolation": "namespaces+seccomp（中-强，视 profile）",
        "cold_start_ms": 50,
        "oom_safe": True,
        "network_iso": True,
        "适合": "高并发自建、数据不出域",
    },
    "Pyodide(WASM)": {
        "isolation": "WASM（强）",
        "cold_start_ms": 100,
        "oom_safe": True,
        "network_iso": True,
        "适合": "浏览器端 Agent，不支持 C 扩展",
    },
}
