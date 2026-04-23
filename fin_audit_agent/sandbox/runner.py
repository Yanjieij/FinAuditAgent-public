"""本地沙箱执行器（用户选择的 RestrictedPython 轻量路径）。

⚠️ **严重警告（面试必讲）**：

    ``RestrictedPython`` **不是生产级的安全沙箱**！它是编译期改写 AST，拦截了
    ``__import__`` / ``open`` / ``exec`` / ``eval`` 等危险原语，但——

    - 无法防止死循环 / OOM（进程内执行，没资源限额）
    - 无法防止 C 扩展里的内存越界
    - 无法 100% 防止 AST 改写绕过（社区披露过 CVE）

    它在本项目的定位是：**本地 demo 跑得通 + 面试可讲编译期防御的原理**。
    生产必须换 :mod:`e2b_runner` 或 Docker + gVisor / nsjail + seccomp。

    面试怎么讲：
        "我在 macOS 本地用 RestrictedPython 跑 demo，生产环境我会换成 nsjail + seccomp +
         network=none 的方案，冷启 50ms；或者 e2b 这种商业云沙箱，独立 MicroVM 级隔离。"

设计要点：
    1. 每次执行分配一个 exec_id
    2. 在执行环境里预置 ``pd / np / Decimal`` 等财务常用库
    3. 约定用户代码用赋值方式导出 cell：``REVENUE = df['revenue'].sum()``
       —— runner 会自动抽取所有**全大写变量**作为 cells，供 Drafter 引用
    4. 超时用线程 + ``signal.alarm``（Linux/macOS 可；Windows 降级为无超时 + 警告）
"""

from __future__ import annotations

import io
import signal
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal, getcontext
from typing import Any

from .artifact_store import new_exec_id, save_dataframe, save_json_cells
from .result_schema import Artifact, ExecResult


class SandboxTimeout(Exception):
    """沙箱执行超时。"""


def _timeout_handler(signum, frame):  # noqa: D401 - signal 回调签名固定
    raise SandboxTimeout("sandbox execution timed out")


def _build_safe_globals() -> dict[str, Any]:
    """构造允许的全局变量。只放财务计算必要的库，不放 os / subprocess / requests。

    为什么用 RestrictedPython 的 ``safe_globals`` 而不是裸 ``{}``：
        RestrictedPython 编译后的代码依赖 ``_getiter_`` / ``_getitem_`` 这些
        helper，必须从它的 ``safe_globals`` 里拿；裸 dict 会报 NameError。
    """
    try:
        from RestrictedPython import safe_globals  # type: ignore
        from RestrictedPython.Eval import default_guarded_getiter
        from RestrictedPython.Guards import (
            guarded_iter_unpack_sequence,
            safe_builtins,
        )
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "RestrictedPython 未安装。请 `conda activate fin-audit-agent` 或 `pip install RestrictedPython`。"
        ) from e

    # Decimal 强制 28 位精度，保证财务计算不丢精度
    getcontext().prec = 28

    g = dict(safe_globals)
    g["_getiter_"] = default_guarded_getiter
    g["_iter_unpack_sequence_"] = guarded_iter_unpack_sequence
    g["__builtins__"] = safe_builtins

    # 预置财务常用库（可选依赖延迟 import，失败就 None）
    try:
        import pandas as pd
        import numpy as np
        g["pd"] = pd
        g["np"] = np
    except ImportError:
        pass

    g["Decimal"] = Decimal

    # 一个显式的 print（RestrictedPython 默认没有）
    def _guarded_print(*args, **kwargs):
        print(*args, **kwargs)

    g["print"] = _guarded_print
    return g


def run_code(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout_sec: int = 10,
) -> ExecResult:
    """在 RestrictedPython 沙箱里跑一段代码。

    Args:
        code: 待执行的 Python 代码字符串。约定：**全大写变量会被抽为 cells**。
        inputs: 可选注入的变量（例如从 SQL 工具来的 DataFrame）。key 必须是合法标识符。
        timeout_sec: 超时秒数，默认 10s。

    Returns:
        一个 :class:`ExecResult`，包含 stdout / cells / artifacts / 成功标志。

    示例::

        result = run_code('''
        import pandas as pd
        df = pd.DataFrame({"amt": [100, 200, 300]})
        TOTAL = df["amt"].sum()        # 会被抽到 cells["TOTAL"]
        ''')
        assert result.cells["TOTAL"] == 600
    """
    from RestrictedPython import compile_restricted  # type: ignore

    exec_id = new_exec_id()
    started = time.perf_counter()
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

    # 构造执行命名空间
    g = _build_safe_globals()
    local_ns: dict[str, Any] = dict(inputs or {})

    # 安装超时（仅 Unix）
    old_handler = None
    if sys.platform != "win32":
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)

    try:
        byte_code = compile_restricted(code, "<sandbox>", "exec")
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(byte_code, g, local_ns)  # noqa: S102 - 这就是沙箱本箱
    except SandboxTimeout as e:
        return ExecResult(
            exec_id=exec_id,
            ok=False,
            stdout=stdout_buf.getvalue()[:8192],
            stderr=stderr_buf.getvalue()[:8192],
            wall_time_ms=(time.perf_counter() - started) * 1000,
            error=f"Timeout({timeout_sec}s): {e}",
        )
    except Exception:
        return ExecResult(
            exec_id=exec_id,
            ok=False,
            stdout=stdout_buf.getvalue()[:8192],
            stderr=stderr_buf.getvalue()[:8192],
            wall_time_ms=(time.perf_counter() - started) * 1000,
            error=traceback.format_exc(limit=3),
        )
    finally:
        # 关闭闹钟 + 还原 handler
        if sys.platform != "win32":
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

    # 抽取 cells（全大写变量）和 artifacts（DataFrame / Figure）
    cells: dict[str, Any] = {}
    artifacts: list[Artifact] = []
    for name, value in local_ns.items():
        if name.startswith("_") or not name.isupper():
            continue
        # DataFrame 落盘而不是塞 cell
        if _is_dataframe(value):
            artifacts.append(save_dataframe(exec_id, name.lower(), value))
            cells[name] = f"<DataFrame rows={len(value)} → artifact>"
        else:
            # 只保留可 JSON 序列化的基本类型；其它转 repr
            cells[name] = _jsonable(value)

    save_json_cells(exec_id, cells)

    return ExecResult(
        exec_id=exec_id,
        ok=True,
        stdout=stdout_buf.getvalue()[:8192],
        stderr=stderr_buf.getvalue()[:8192],
        wall_time_ms=(time.perf_counter() - started) * 1000,
        artifacts=artifacts,
        cells=cells,
    )


def _is_dataframe(value: Any) -> bool:
    """检测 value 是否是 pandas DataFrame，不强依赖 pandas。"""
    try:
        import pandas as pd
        return isinstance(value, pd.DataFrame)
    except ImportError:
        return False


def _jsonable(value: Any) -> Any:
    """尽量返回 JSON 友好的值，否则 repr。Decimal 特别处理为字符串保留精度。"""
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)
