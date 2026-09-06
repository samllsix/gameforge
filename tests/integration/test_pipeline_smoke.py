"""全链路离线冒烟测试（GameFactory-3A 风格 CPU-only harness）。

GAMEFORGE_LLM_STUB=1 时，所有 Agent 走模板降级路径，
整条工作流无需网络、无需 API key、无需 Godot 即可跑通。
本测试断言：
1. 工作流跑完不崩溃，产出代码与场景
2. 产物落盘位置符合 src/core/paths.py 的约定
3. Scene IR 按约定写入 projects/<pid>/.scene_ir.json
"""
import asyncio
import shutil

import pytest

import src.core.paths as paths
from src.core.graph.workflow import GameDevWorkflow


@pytest.fixture()
def smoke_env(tmp_path, monkeypatch):
    """stub 模式 + 隔离的 projects 根 + 无 Godot 的最小配置。"""
    monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
    projects = tmp_path / "projects"
    shutil.rmtree(projects, ignore_errors=True)
    monkeypatch.setattr(paths, "PROJECTS_ROOT", projects)
    llm_mod = pytest.importorskip("src.utils.llm_client")
    llm_mod._client_cache.clear()

    pid = "e2e_smoke_offline"
    cfg = {
        "godot": {
            # 指向 paths 管理的项目目录；editor 指向不存在的路径 → 编译自动跳过
            "editor_path": str(tmp_path / "no_godot"),
            "project_path": str(projects / pid),
            "compile_mode": "auto",
            # Godot HTTP 插件不在线 → scene_generator 走磁盘回退写 .tscn
            "auto_build_scene": True,
        },
        "sandbox": {"enabled": False},
        "recipes": {"enabled": False},
        "runtime_smoke": {"skip_when_unavailable": True},
    }
    yield {"cfg": cfg, "pid": pid, "projects": projects}
    llm_mod._client_cache.clear()


def _run_workflow(env) -> dict:
    wf = GameDevWorkflow(env["cfg"])
    state = asyncio.run(wf.run({
        "project_context": {
            "requirements": "一个简单的平台跳跃游戏，玩家可以左右移动和跳跃，收集金币",
            # preview project_id 从 project_name 解析（workflow._resolve_preview_project_id）
            "project_name": env["pid"],
        },
    }))
    return state


class TestOfflineSmoke:
    def test_workflow_completes_with_generated_code(self, smoke_env):
        state = _run_workflow(smoke_env)
        # Agent 链至少产出：GDM、任务计划、代码文件
        assert state.get("game_design_model"), "game_designer 应产出 GDM（模板降级）"
        assert state.get("task_plan"), "planner 应产出任务计划"
        code = state.get("code_generated") or {}
        gd_files = [k for k in code if k.endswith(".gd")]
        assert gd_files, f"code_generator 应产出 .gd 文件，实际: {list(code)[:5]}"

    def test_scene_ir_lands_at_paths_convention(self, smoke_env):
        _run_workflow(smoke_env)
        pid = smoke_env["pid"]
        # 两条路径之一：主项目根或沙箱任务工作区；stub 冒烟走主项目根
        ir_path = paths.scene_ir_path(pid)
        assert ir_path.is_file(), f"Scene IR 应落盘到 {ir_path}"
        payload = paths.read_json(ir_path)
        assert payload and payload.get("project_id") == pid
        assert payload.get("scene_ir"), "Scene IR 内容不应为空"

    def test_scene_files_written_into_project_dir(self, smoke_env):
        state = _run_workflow(smoke_env)
        pid = smoke_env["pid"]
        scene_path = state.get("scene_path") or ""
        project_dir = paths.project_dir(pid)
        tscn = list(project_dir.rglob("*.tscn"))
        assert tscn, f"项目目录内应有 .tscn 场景文件（scene_path={scene_path!r}）"

    def test_is_deterministic_across_runs(self, smoke_env):
        """stub 模式应可重复：两次运行产出相同文件集合（离线冒烟的意义所在）。"""
        first = _run_workflow(smoke_env)
        keys_first = sorted((first.get("code_generated") or {}).keys())
        second = _run_workflow(smoke_env)
        keys_second = sorted((second.get("code_generated") or {}).keys())
        assert keys_first == keys_second
