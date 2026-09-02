"""预览无任务空闲自动停止（supervisor idle shutdown）单元测试"""

import time

import pytest

from src.engine.godot.supervisor import GodotSupervisor, ProjectProc


def _make_supervisor(idle_timeout: float = 30) -> GodotSupervisor:
    return GodotSupervisor({
        "preview": {"enabled": True, "idle_timeout_seconds": idle_timeout},
    })


@pytest.mark.asyncio
async def test_idle_process_stopped_after_timeout():
    sup = _make_supervisor(idle_timeout=30)
    pp = ProjectProc(project_id="proj_x", project_path="/tmp/x", port=18769)
    pp.last_used_at = time.time() - 60  # 60s 无帧请求
    sup._procs["proj_x"] = pp

    stopped = []

    async def fake_stop(pid):
        stopped.append(pid)

    sup.stop = fake_stop

    await sup._health_check_one("proj_x")
    assert stopped == ["proj_x"]


@pytest.mark.asyncio
async def test_active_process_not_stopped():
    sup = _make_supervisor(idle_timeout=30)
    pp = ProjectProc(project_id="proj_x", project_path="/tmp/x", port=18769)
    pp.last_used_at = time.time()  # 刚请求过帧，视为活跃
    sup._procs["proj_x"] = pp

    stopped = []

    async def fake_stop(pid):
        stopped.append(pid)

    sup.stop = fake_stop

    await sup._health_check_one("proj_x")
    assert stopped == []


def test_idle_timeout_default():
    sup = GodotSupervisor({"preview": {}})
    assert sup.idle_timeout == 30.0
    sup2 = _make_supervisor(idle_timeout=5)
    assert sup2.idle_timeout == 5.0
