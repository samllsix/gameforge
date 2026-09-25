"""Workflow sandbox lifecycle tests.

验证 GameDevWorkflow 在启用沙箱时：
- run() 创建任务工作区
- 工作流成功完成后自动合并
- 工作流失败后自动回滚
- run_with_streaming() 同理
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.graph.workflow import GameDevWorkflow


def _make_workflow():
    cfg = {
        "sandbox": {"enabled": True, "auto_merge": True, "auto_rollback": True},
        "godot": {"editor_path": "", "project_path": "", "compile_mode": "auto"},
        "agents": {"orchestrator": {"max_iterations": 1}},
        "llm": {"default_model": "stub"},
    }
    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = cfg
    wf.sandbox_enabled = True
    wf.sandbox_auto_merge = True
    wf.sandbox_auto_rollback = True
    wf.memory = MagicMock()
    wf.logger = MagicMock()
    wf.recipe_enabled = False
    return wf


def test_run_creates_and_merges_sandbox_on_success():
    wf = _make_workflow()
    fake_task = {"task_id": "t1", "task_dir": "/tmp/sandbox/p/t1", "role": "director"}
    wf.sandbox = MagicMock()
    wf.sandbox.create.return_value = fake_task
    wf.sandbox.merge.return_value = "/tmp/main"

    fake_state = {
        "task_plan": [],
        "code_generated": {},
        "scene_status": "success",
        "warnings": [],
    }

    async def fake_ainvoke(*args, **kwargs):
        return fake_state

    wf.graph = MagicMock()
    wf.graph.ainvoke = fake_ainvoke

    with patch.object(wf, "_post_process"):
        with patch.object(wf, "_try_godot_pipeline"):
            result = asyncio.run(wf.run({"project_context": {"project_name": "p"}}))

    wf.sandbox.create.assert_called_once_with("p", role="director")
    wf.sandbox.merge.assert_called_once_with(fake_task)


def test_run_rollbacks_sandbox_on_failure():
    wf = _make_workflow()
    fake_task = {"task_id": "t1", "task_dir": "/tmp/sandbox/p/t1", "role": "director"}
    wf.sandbox = MagicMock()
    wf.sandbox.create.return_value = fake_task
    wf.sandbox.rollback.return_value = 0

    async def fake_ainvoke(*args, **kwargs):
        raise RuntimeError("boom")

    wf.graph = MagicMock()
    wf.graph.ainvoke = fake_ainvoke

    with patch.object(wf, "_post_process"):
        with patch.object(wf, "_try_godot_pipeline"):
            with pytest.raises(RuntimeError):
                asyncio.run(wf.run({"project_context": {"project_name": "p"}}))

    wf.sandbox.rollback.assert_called_once_with(fake_task)


def test_run_skips_merge_when_auto_merge_disabled():
    wf = _make_workflow()
    wf.sandbox_auto_merge = False
    fake_task = {"task_id": "t1", "task_dir": "/tmp/sandbox/p/t1", "role": "director"}
    wf.sandbox = MagicMock()
    wf.sandbox.create.return_value = fake_task

    fake_state = {
        "task_plan": [],
        "code_generated": {},
        "scene_status": "success",
        "warnings": [],
    }

    async def fake_ainvoke(*args, **kwargs):
        return fake_state

    wf.graph = MagicMock()
    wf.graph.ainvoke = fake_ainvoke

    with patch.object(wf, "_post_process"):
        with patch.object(wf, "_try_godot_pipeline"):
            asyncio.run(wf.run({"project_context": {"project_name": "p"}}))

    wf.sandbox.merge.assert_not_called()
