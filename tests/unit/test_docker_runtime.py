"""Docker 后端单元测试。

当 Docker 可用时，验证 run_in_container 的基本行为；
当 Docker 不可用时，验证优雅降级。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.sandbox.runtime.process import RuntimePolicy
from src.sandbox.runtime.docker_runtime import run_in_container, _DOCKER_AVAILABLE


def _has_docker() -> bool:
    if not _DOCKER_AVAILABLE:
        return False
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _has_docker(), reason="Docker 不可用，跳过 Docker 后端测试")
def test_run_in_container_echo():
    policy = RuntimePolicy(timeout_seconds=30)
    result = run_in_container(
        cmd=["echo", "-n", "hello-docker"],
        policy=policy,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        image="alpine:latest",
        network_disabled=True,
        mem_limit="256m",
        cpu_quota=50000,
        pids_limit=8,
    )
    assert result.success is True
    assert result.exit_code == 0
    assert "hello-docker" in result.stdout


@pytest.mark.skipif(not _has_docker(), reason="Docker 不可用，跳过 Docker 后端测试")
def test_run_in_container_nonzero_exit():
    policy = RuntimePolicy(timeout_seconds=30)
    result = run_in_container(
        cmd=["sh", "-c", "exit 42"],
        policy=policy,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        image="alpine:latest",
        network_disabled=True,
    )
    assert result.success is False
    assert result.exit_code == 42


def test_docker_runtime_import_fails_gracefully():
    """当 docker 包未安装时，应给出明确错误。"""
    import src.sandbox.runtime.docker_runtime as docker_mod
    # 如果 docker 可用，这个测试没有意义；直接跳过
    if _DOCKER_AVAILABLE:
        pytest.skip("Docker SDK 已安装，无法测试导入失败")
    assert docker_mod._DOCKER_AVAILABLE is False
