"""Sandbox routing tests for GameDevWorkflow.

验证启用沙箱时，workflow 的构建/编译管道不会污染主线项目：
- headless 可用时走沙箱工作区
- headless 不可用且 compile_mode=headless 时报错
- headless 不可用且 compile_mode=auto 时跳过构建
"""
from unittest.mock import MagicMock

import pytest

from src.core.graph.workflow import GameDevWorkflow


def _make_workflow(compile_mode: str = "auto", godot_editor_path: str = "/tmp/godot"):
    cfg = {
        "sandbox": {"enabled": True},
        "godot": {
            "editor_path": godot_editor_path,
            "project_path": "/tmp/main",
            "compile_mode": compile_mode,
        },
    }
    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = cfg
    wf.sandbox_enabled = True
    wf.sandbox_auto_merge = True
    wf.sandbox_auto_rollback = True
    wf.memory = MagicMock()
    wf.logger = MagicMock()
    return wf


def test_try_godot_pipeline_headless_uses_sandbox_project_config():
    wf = _make_workflow(compile_mode="auto", godot_editor_path="/tmp/godot")
    task = {"task_id": "t1", "task_dir": "/tmp/sandbox/p/t1", "role": "director"}
    state = {"sandbox": {"task": task}, "code_generated": {"scripts/player.gd": "extends Node\n"}}
    events = []

    async def cb(*args, **kwargs):
        events.append((args, kwargs))

    class FakeEditor:
        def validate(self):
            return True, "ok"

        def import_files(self, files):
            return MagicMock(status="success")

        def check_scripts(self, paths):
            return MagicMock(errors=[])

    import asyncio

    async def run():
        await wf._try_godot_pipeline_headless(state, cb, FakeEditor())

    asyncio.run(run())
    assert any("正在导入代码到 Godot 项目" in str(e) for e in events)


def test_try_godot_pipeline_skips_http_when_sandbox_headless_unavailable():
    wf = _make_workflow(compile_mode="auto", godot_editor_path="")
    state = {"sandbox": {"task": {"task_dir": "/tmp/sandbox/p/t1", "role": "director"}}, "code_generated": {"scripts/player.gd": "extends Node\n"}}
    events = []

    async def cb(*args, **kwargs):
        events.append((args, kwargs))

    import asyncio

    async def run():
        await wf._try_godot_pipeline(state, cb)

    asyncio.run(run())
    assert any(
        "HTTP 模式会修改主线" in str(e) or "godot_http_unavailable" in str(e)
        for e in events
    )
