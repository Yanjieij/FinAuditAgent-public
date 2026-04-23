# 生产沙箱镜像（给 e2b / Docker+gVisor / nsjail 用）
#
# 设计决策：
#   1. 基于 python:3.11-slim（小、启动快）
#   2. 预装财务常用库：pandas / numpy / scipy / matplotlib / decimal（stdlib）
#   3. **不装** requests / httpx / urllib3：因为沙箱运行时 --network=none，装了也没用
#   4. 运行时 entrypoint 接受 stdin 上的 code，执行后把 cells / artifacts 写到 /work
#   5. 用非 root USER（sandbox:sandbox，uid=1000）；容器还会被外层用 --cap-drop=ALL
#      + --read-only + --tmpfs /work:size=100M 进一步限制

FROM python:3.11-slim

RUN useradd -u 1000 -m -s /bin/bash sandbox \
 && mkdir /work \
 && chown sandbox:sandbox /work

# 固定版本便于复现
RUN pip install --no-cache-dir \
    pandas==2.2.2 \
    numpy==1.26.4 \
    scipy==1.13.1 \
    matplotlib==3.9.0 \
    openpyxl==3.1.5

USER sandbox
WORKDIR /work

# 生产容器由外部通过 `docker run --network=none --read-only` 启动；
# 镜像本身只提供环境。entrypoint 由调用方注入。
ENTRYPOINT ["python", "-u"]
