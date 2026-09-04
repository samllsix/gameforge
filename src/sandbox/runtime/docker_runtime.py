"""Docker 后端：在隔离容器里执行命令。

Phase 2 扩展：替代本地 Job Object / rlimit，提供更强的隔离和跨平台一致性。

配置示例（config/config.yaml）：
    sandbox:
      backend: docker
      docker:
        image: gameforge/sandbox-runner:latest
        network: false
        mem_limit: 2g
        cpu_quota: 100000
        pids_limit: 32
"""

import os
import time
from typing import Dict, List, Optional

import structlog

try:
    import docker
    from docker.errors import DockerException, NotFound
    _DOCKER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DOCKER_AVAILABLE = False

from src.sandbox.runtime.process import ExecutionOutcome, RuntimePolicy

logger = structlog.get_logger(__name__)


class DockerRuntimeError(RuntimeError):
    """Docker 后端执行失败。"""


def _docker_client() -> "docker.DockerClient":
    if not _DOCKER_AVAILABLE:
        raise DockerRuntimeError("docker 包未安装，无法使用 Docker 后端")
    try:
        return docker.from_env()
    except DockerException as exc:
        raise DockerRuntimeError(f"Docker 守护进程不可用: {exc}") from exc


def run_in_container(
    cmd: List[str],
    policy: Optional[RuntimePolicy] = None,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    stdout_max: int = 200_000,
    image: str = "gameforge/sandbox-runner:latest",
    network_disabled: bool = True,
    mem_limit: str = "2g",
    cpu_quota: int = 100000,
    pids_limit: int = 32,
) -> ExecutionOutcome:
    """在 Docker 容器里执行命令，返回 ExecutionOutcome。"""
    policy = policy or RuntimePolicy()
    client = _docker_client()

    volumes = {}
    if cwd and os.path.isdir(cwd):
        volumes[cwd] = {"bind": "/workspace", "mode": "rw"}

    # 白名单环境变量
    safe_env = {k: v for k, v in (env or policy.sanitized_env()).items()
                if k.upper() in _ENV_ALLOWLIST}

    container_kwargs = {
        "image": image,
        "command": cmd,
        "volumes": volumes,
        "working_dir": "/workspace" if cwd else None,
        "environment": safe_env or None,
        "network_disabled": network_disabled,
        "mem_limit": mem_limit,
        "cpu_quota": cpu_quota,
        "pids_limit": pids_limit,
        "detach": True,
        "remove": True,
    }
    container_kwargs = {k: v for k, v in container_kwargs.items() if v is not None}

    t0 = time.monotonic()
    container = None
    try:
        container = client.containers.run(**container_kwargs)
        result = container.wait(timeout=policy.timeout_seconds)
        logs = container.logs(stdout=True, stderr=True)
        # Docker logs 返回 bytes，decode 为 str
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        # 简单分割 stdout/stderr（Docker 不区分，统一放 stdout）
        stdout = logs[:stdout_max] if logs else ""
        stderr = ""
        exit_code = int(result.get("StatusCode", 0)) if isinstance(result, dict) else int(result.exit_code)
        elapsed = time.monotonic() - t0
        return ExecutionOutcome(
            success=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=elapsed,
            killed_reason=None,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("docker_runtime_failed", error=str(exc))
        return ExecutionOutcome(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            elapsed_seconds=elapsed,
            killed_reason="docker_error",
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001
                pass


# 复用白名单
_ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "HOME",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)
