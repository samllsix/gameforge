"""Sandbox 隔离执行容器镜像。

提供最小化运行环境，用于在 Docker 容器里执行 Godot / Python 等命令。
默认开启网络隔离（network_disabled），仅通过 volume 挂载任务工作区。
"""

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        godot4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 非 root 用户运行（安全加固）
RUN useradd -m -u 1000 sandbox \
    && chown -R sandbox:sandbox /workspace
USER sandbox

ENTRYPOINT ["python3", "-c"]
CMD ["import sys; print('sandbox runner ready')"]
