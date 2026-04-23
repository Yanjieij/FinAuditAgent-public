"""生产沙箱容器内的 entrypoint（示意）。

用法（外层编排）::

    docker run --rm --network=none --read-only --tmpfs /work:size=100M \\
        --memory=512m --cpus=1 --cap-drop=ALL \\
        -v /host/tmp/exec-ID:/work \\
        fin-audit-sandbox:latest /work/entrypoint.py

容器内：
    - 从 /work/code.py 读代码
    - exec() 并抽取 cells / artifacts
    - 写回 /work/result.json，外层 runner 读走
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, getcontext
from pathlib import Path


def main() -> int:
    WORK = Path("/work")
    code_path = WORK / "code.py"
    if not code_path.exists():
        print(json.dumps({"ok": False, "error": "missing /work/code.py"}))
        return 2

    getcontext().prec = 28

    ns: dict = {}
    try:
        exec(compile(code_path.read_text(), "<sandbox>", "exec"), ns)
    except Exception as e:
        (WORK / "result.json").write_text(json.dumps(
            {"ok": False, "error": repr(e)}, ensure_ascii=False
        ))
        return 1

    # 抽全大写变量为 cells
    cells = {
        k: (str(v) if isinstance(v, Decimal) else
            (v if isinstance(v, (int, float, str, bool, type(None))) else repr(v)))
        for k, v in ns.items()
        if k.isupper() and not k.startswith("_")
    }
    (WORK / "result.json").write_text(json.dumps(
        {"ok": True, "cells": cells}, ensure_ascii=False
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
