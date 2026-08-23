"""P0-2 workflow 接入测试。

不跑完整 workflow（避免拉 LLM），用 stub 验证 _runtime_smoke_test 的路由：
- 无 scene_path → 跳过、runnable=None
- scene_path 存在但 Godot 不可用 → runnable=True（降级）
- scene_path 存在且冒烟失败 → 触发 debugger
"""
import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.graph.workflow import GameDevWorkflow


def _make_workflow():
    """用最小 config 构造一个 workflow 实例（不需要真实 LLM）"""
    config = {
        "godot": {"editor_path": "", "project_path": "", "compile_mode": "auto"},
        "runtime_smoke": {"skip_when_unavailable": True, "frames": 5, "timeout_seconds": 3},
        "llm": {"default_model": "stub"},
    }
    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = config
    wf.memory = MagicMock()
    return wf


async def test_runtime_smoke_skips_when_no_scene_path():
    wf = _make_workflow()
    events: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {"warnings": []}

    async def cb(event_type, data):
        events.append({"type": event_type, "data": data})

    summary = await wf._runtime_smoke_test(state, cb)

    assert summary["runnable"] is None
    assert summary["runtime_smoke_skipped"] is True
    # 不应发 phase_start（无 scene 不值得跑）
    assert not any(e["type"] == "phase_start" for e in events)


async def test_runtime_smoke_passes_when_godot_unavailable():
    """Godot 二进制缺失 + skip_when_unavailable=True → runnable=True（降级）"""
    wf = _make_workflow()
    state: Dict[str, Any] = {
        "scene_path": "res://scenes/Main.tscn",
        "warnings": [],
    }
    events: List[Dict[str, Any]] = []

    async def cb(event_type, data):
        events.append({"type": event_type, "data": data})

    summary = await wf._runtime_smoke_test(state, cb)

    # 降级默认通过，避免没装 Godot 的环境被误判
    assert summary["runnable"] is True
    assert summary["runtime_smoke_attempts"] == 1
    assert events[-1]["type"] == "runtime_smoke_result"
    assert events[-1]["data"]["runnable"] is True
    assert events[-1]["data"]["scene_path"] == "res://scenes/Main.tscn"


async def test_runtime_smoke_triggers_debugger_on_failure():
    """冒烟失败 → 调用 debugger，max_fix_attempts 之后才放弃"""
    wf = _make_workflow()
    state: Dict[str, Any] = {
        "scene_path": "res://scenes/Broken.tscn",
        "warnings": [],
        "code_generated": {"scripts/foo.gd": "extends Node\n"},
    }

    # 用真实 GodotRuntimeSmoke 类，但 stub 掉 .run_scene 让它返回失败
    fail_result = {
        "runnable": False,
        "exit_code": 1,
        "errors": [{"pattern": "SCRIPT ERROR", "snippet": "Parse Error at foo.gd:5"}],
        "warnings": [],
        "frame_count": 0,
        "elapsed_seconds": 0.3,
        "scene_path": "res://scenes/Broken.tscn",
    }

    events: List[Dict[str, Any]] = []

    async def cb(event_type, data):
        events.append({"type": event_type, "data": data})

    # stub debugger_node 让它返回空字典（避免拉 LLM）
    wf._debugger_node = AsyncMock(return_value={})
    # stub GodotEditor 防止真读盘
    with patch("src.engine.godot.GodotEditor") as FakeEditor:
        with patch("src.engine.godot.runtime_smoke.GodotRuntimeSmoke") as FakeSmoke:
            fake_instance = MagicMock()
            fake_instance.run_scene = MagicMock(return_value=fail_result)
            FakeSmoke.return_value = fake_instance
            summary = await wf._runtime_smoke_test(state, cb, max_fix_attempts=1)

    assert summary["runnable"] is False
    assert summary["runtime_smoke_attempts"] == 2  # 1 次 + 1 次修复后再跑
    # 至少 2 个 runtime_smoke_result 事件
    rsr = [e for e in events if e["type"] == "runtime_smoke_result"]
    assert len(rsr) == 2
    assert all(e["data"]["runnable"] is False for e in rsr)
    # debugger 被调用
    assert wf._debugger_node.call_count == 1


async def test_runtime_smoke_succeeds_first_try():
    """冒烟一次过 → 不调 debugger，runnable=True"""
    wf = _make_workflow()
    state: Dict[str, Any] = {
        "scene_path": "res://scenes/Good.tscn",
        "warnings": [],
    }
    pass_result = {
        "runnable": True,
        "exit_code": 0,
        "errors": [],
        "warnings": [],
        "frame_count": 60,
        "elapsed_seconds": 1.0,
        "scene_path": "res://scenes/Good.tscn",
    }
    wf._debugger_node = AsyncMock(return_value={})

    events: List[Dict[str, Any]] = []

    async def cb(event_type, data):
        events.append({"type": event_type, "data": data})

    with patch("src.engine.godot.runtime_smoke.GodotRuntimeSmoke") as FakeSmoke:
        fake_instance = MagicMock()
        fake_instance.run_scene = MagicMock(return_value=pass_result)
        FakeSmoke.return_value = fake_instance
        summary = await wf._runtime_smoke_test(state, cb)

    assert summary["runnable"] is True
    assert summary["runtime_smoke_attempts"] == 1
    assert wf._debugger_node.call_count == 0
    assert summary["runtime_smoke_errors"] == []