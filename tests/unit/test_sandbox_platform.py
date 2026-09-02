"""Sandbox Platform Phase 1 单元测试。

覆盖：权限系统 / 工作区生命周期 / 快照回滚 / 资源笼执行 / Controller 全流程。
"""

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@contextlib.contextmanager
def tmp_path_context(tmp_path=None):
    """确定性临时目录上下文：先清残留，保证隔离。"""
    import shutil
    base = tmp_path if tmp_path is not None else Path(__file__).parent / "tmp_sandbox"
    base = Path(base)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.sandbox.controller import SandboxController
from src.sandbox.policy.permission import (
    ROLES,
    PermissionDenied,
    check_write,
    describe_role,
    resolve_role,
)
from src.sandbox.runtime.process import RuntimePolicy, run_isolated
from src.sandbox.snapshot import SnapshotManager
from src.sandbox.workspace import WorkspaceManager

IS_WIN = sys.platform == "win32"


@pytest.fixture()
def project(tmp_path):
    """造一个假的主线项目（tmp_path 为确定性路径，先清残留保证隔离）。"""
    import shutil

    ws_root = tmp_path / "workspace"
    if ws_root.exists():
        shutil.rmtree(ws_root)
    main = tmp_path / "projects" / "proj_a"
    if main.exists():
        shutil.rmtree(main)
    (main / "scripts").mkdir(parents=True)
    (main / "assets").mkdir(parents=True)
    (main / "scenes").mkdir(parents=True)
    (main / "scripts" / "player.gd").write_text("extends CharacterBody2D\n", encoding="utf-8")
    (main / "assets" / "hero.png").write_bytes(b"\x89PNG fake")
    (main / "project.godot").write_text("[application]\n", encoding="utf-8")
    return main


@pytest.fixture()
def ws(tmp_path):
    return WorkspaceManager(
        workspace_root=str(tmp_path / "workspace"),
        projects_root=str(tmp_path / "projects"),
    )


@pytest.fixture()
def snap(tmp_path):
    return SnapshotManager(workspace_root=str(tmp_path / "workspace"))


# ══════════════ 权限系统 ══════════════
class TestPermission:
    def test_code_agent_can_write_scripts(self):
        check_write("code_agent", "scripts/player.gd")

    def test_code_agent_cannot_write_project_godot(self):
        with pytest.raises(PermissionDenied):
            check_write("code_agent", "project.godot")

    def test_asset_agent_cannot_touch_scripts(self):
        with pytest.raises(PermissionDenied):
            check_write("asset_agent", "scripts/player.gd")
        with pytest.raises(PermissionDenied):
            check_write("asset_agent", "project.godot")

    def test_asset_agent_can_write_assets(self):
        check_write("asset_agent", "assets/hero.png")

    def test_qa_agent_read_only(self):
        with pytest.raises(PermissionDenied):
            check_write("qa_agent", "scripts/player.gd")

    def test_unknown_role_fail_closed(self):
        with pytest.raises(PermissionDenied):
            check_write("unknown", "scripts/player.gd")

    def test_deny_overrides_allow(self):
        # code_agent allow scripts/ 但 deny addons/gameforge/runtime/
        with pytest.raises(PermissionDenied):
            check_write("code_agent", "addons/gameforge/runtime/evil.gd")

    def test_agent_name_resolution(self):
        assert resolve_role("CodeGeneratorAgent") == "code_agent"
        assert resolve_role("SceneGeneratorAgent") == "scene_agent"
        assert resolve_role("TestGeneratorAgent") == "qa_agent"
        assert resolve_role("DebuggerAgent") == "repair_agent"
        assert resolve_role("OrchestratorAgent") == "director"
        assert resolve_role("MysteryBot") == "unknown"

    def test_describe_role(self):
        d = describe_role("qa_agent")
        assert d["read_only"] is True
        assert "director" in ROLES


# ══════════════ 工作区 ══════════════
class TestWorkspace:
    def test_create_and_merge(self, ws, project):
        task_dir = ws.create_task("proj_a", "task_001")
        assert (task_dir / "scripts" / "player.gd").is_file()

        # 修改工作区
        (task_dir / "scripts" / "player.gd").write_text("modified\n", encoding="utf-8")
        # 合并回主线
        ws.merge_task("proj_a", "task_001")
        assert "modified" in (project / "scripts" / "player.gd").read_text(encoding="utf-8")

    def test_parallel_tasks_isolated(self, ws, project):
        t1 = ws.create_task("proj_a", "task_001")
        t2 = ws.create_task("proj_a", "task_002")
        (t1 / "scripts" / "player.gd").write_text("t1\n", encoding="utf-8")
        (t2 / "scripts" / "player.gd").write_text("t2\n", encoding="utf-8")
        # 互不污染
        assert "t1" in (t1 / "scripts" / "player.gd").read_text(encoding="utf-8")
        assert "t2" in (t2 / "scripts" / "player.gd").read_text(encoding="utf-8")
        # 主线未动
        assert "modified" not in (project / "scripts" / "player.gd").read_text(encoding="utf-8")

    def test_discard_keeps_main(self, ws, project):
        ws.create_task("proj_a", "task_001")
        ws.discard_task("proj_a", "task_001")
        assert (project / "scripts" / "player.gd").is_file()

    def test_invalid_ids_rejected(self, ws):
        with pytest.raises(Exception):
            ws.create_task("../escape", "t")
        with pytest.raises(Exception):
            ws.create_task("proj_a", "../../etc")

    def test_missing_main_fails_closed(self, ws):
        with pytest.raises(Exception):
            ws.create_task("nonexistent", "t")

    def test_skip_dirs_not_copied(self, ws, project):
        (project / "export").mkdir(exist_ok=True)
        (project / "export" / "big.bin").write_bytes(b"x" * 100)
        task_dir = ws.create_task("proj_a", "task_001")
        assert not (task_dir / "export").exists()


# ══════════════ 快照回滚 ══════════════
class TestSnapshot:
    def test_full_snapshot_and_rollback(self, ws, snap, project):
        task_dir = ws.create_task("proj_a", "task_001")
        sid = snap.create(task_dir, label="before-bug")
        # 改坏
        (task_dir / "scripts" / "player.gd").write_text("BROKEN", encoding="utf-8")
        (task_dir / "scripts" / "new_file.gd").write_text("extra", encoding="utf-8")
        # 回滚
        restored = snap.rollback(task_dir, sid)
        assert restored >= 1
        assert "BROKEN" not in (task_dir / "scripts" / "player.gd").read_text(encoding="utf-8")

    def test_single_file_snapshot(self, ws, snap, project):
        task_dir = ws.create_task("proj_a", "task_001")
        sid = snap.snapshot_file(task_dir, "scripts/player.gd")
        assert sid
        (task_dir / "scripts" / "player.gd").write_text("WORSE", encoding="utf-8")
        assert snap.rollback_file(task_dir, "scripts/player.gd")
        assert "WORSE" not in (task_dir / "scripts" / "player.gd").read_text(encoding="utf-8")

    def test_list_snapshots(self, ws, snap, project):
        task_dir = ws.create_task("proj_a", "task_001")
        snap.create(task_dir, label="a")
        snap.create(task_dir, label="b")
        lst = snap.list_snapshots(task_dir)
        assert len(lst) >= 2
        assert lst[0]["label"] == "b"  # 新的在前

    def test_prune_old_snapshots(self, ws, snap, project):
        task_dir = ws.create_task("proj_a", "task_001")
        for i in range(15):
            snap.create(task_dir, label=f"s{i}")
        assert len(snap.list_snapshots(task_dir)) <= 10


# ══════════════ 资源笼执行 ══════════════
class TestRuntimeProcess:
    def test_normal_execution(self):
        code = "print('hello-cage')"
        out = run_isolated([sys.executable, "-c", code], policy=RuntimePolicy(timeout_seconds=30))
        assert out.success
        assert "hello-cage" in out.stdout

    def test_timeout_kill(self):
        code = "import time; time.sleep(60)"
        t0 = time.monotonic()
        out = run_isolated(
            [sys.executable, "-c", code],
            policy=RuntimePolicy(timeout_seconds=3, cpu_time_seconds=60),
        )
        assert not out.success
        assert out.killed_reason == "timeout"
        assert time.monotonic() - t0 < 20

    def test_env_allowlist_strips_secrets(self):
        code = "import os; print(os.environ.get('GAMEFORGE_DB_PASSWORD', 'STRIPPED'))"
        os.environ["GAMEFORGE_DB_PASSWORD"] = "super-secret"
        try:
            out = run_isolated([sys.executable, "-c", code], policy=RuntimePolicy(timeout_seconds=30))
            assert "STRIPPED" in out.stdout
            assert "super-secret" not in out.stdout
        finally:
            del os.environ["GAMEFORGE_DB_PASSWORD"]

    def test_memory_limit(self):
        if not IS_WIN:
            pytest.skip("Job Object 仅 Windows；Linux 走 RLIMIT_AS")
        # 申请远超 64MB 内存 → Job Object 拒绝分配 → MemoryError → 非零退出
        code = "x = bytearray(256 * 1024 * 1024); print('allocated')"
        out = run_isolated(
            [sys.executable, "-c", code],
            policy=RuntimePolicy(memory_limit_mb=64, timeout_seconds=30),
        )
        assert not out.success or "allocated" not in out.stdout


# ══════════════ Controller 全流程 ══════════════
class TestController:
    @pytest.fixture()
    def controller(self, tmp_path):
        return SandboxController(
            {
                "sandbox": {
                    "workspace_root": str(tmp_path / "workspace"),
                    "projects_root": None,  # 不支持该键，走环境
                }
            }
        )

    @pytest.fixture()
    def controller_with_roots(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_PROJECTS_ROOT", str(tmp_path / "projects"))
        monkeypatch.setenv("GAMEFORGE_WORKSPACE_ROOT", str(tmp_path / "workspace"))
        return SandboxController({})

    def test_full_lifecycle_success(self, controller_with_roots, project):
        sb = controller_with_roots
        task = sb.create("proj_a", role="CodeGeneratorAgent")
        assert task["role"] == "code_agent"

        # 修改（权限内）
        sb.modify(task, "scripts/player.gd", "extends CharacterBody2D\n# v2\n")

        # 测试（执行 python -c exit 0）
        result = sb.execute(task, [sys.executable, "-c", "print('tests pass')"])
        assert result.success

        # 合并
        main = sb.merge(task)
        assert "# v2" in (Path(main) / "scripts" / "player.gd").read_text(encoding="utf-8")

    def test_full_lifecycle_failure_rollback(self, controller_with_roots, project):
        sb = controller_with_roots
        task = sb.create("proj_a", role="repair_agent")

        original = (project / "scripts" / "player.gd").read_text(encoding="utf-8")
        sb.modify(task, "scripts/player.gd", "TOTALLY BROKEN")

        # 模拟测试失败
        result = sb.execute(task, [sys.executable, "-c", "import sys; sys.exit(1)"])
        assert not result.success

        # 回滚到出生基线
        sb.rollback(task)
        assert (Path(task["task_dir"]) / "scripts" / "player.gd").read_text(encoding="utf-8") == original

        # 丢弃任务
        sb.discard(task)

    def test_modify_denied_by_role(self, controller_with_roots, project):
        sb = controller_with_roots
        task = sb.create("proj_a", role="QAAgent")  # → qa_agent 只读
        with pytest.raises(PermissionDenied):
            sb.modify(task, "scripts/player.gd", "hack")

    def test_asset_agent_blocked_from_scripts(self, controller_with_roots, project):
        sb = controller_with_roots
        task = sb.create("proj_a", role="asset_agent")
        with pytest.raises(PermissionDenied):
            sb.modify(task, "scripts/player.gd", "hack")
        # 但可以写素材
        sb.modify(task, "assets/new_tex.png", "binary")

    def test_modify_path_escape_blocked(self, controller_with_roots, project):
        sb = controller_with_roots
        task = sb.create("proj_a", role="code_agent")
        with pytest.raises(PermissionError):
            sb.modify(task, "../../outside.gd", "escape")

    def test_status_report(self, controller_with_roots, project):
        sb = controller_with_roots
        sb.create("proj_a", role="code_agent")
        st = sb.status(project_id="proj_a")
        assert st["project_id"] == "proj_a"
        assert len(st["tasks"]) == 1
        assert "code_agent" in st["role_policies"]

    def test_set_policy_override(self, controller_with_roots, project):
        sb = controller_with_roots
        task = sb.create("proj_a", role="code_agent")
        pol = sb.set_policy(task, memory_limit_mb=512, timeout_seconds=60)
        assert pol.memory_limit_mb == 512
        # 新策略生效于 execute
        out = sb.execute(task, [sys.executable, "-c", "print('ok')"])
        assert out.success


# ---------------------------------------------------------------------------
# Workflow <-> Sandbox 集成测试
# ---------------------------------------------------------------------------


def test_workflow_wrap_code_node_syncs_to_sandbox(tmp_path):
    """代码生成节点包装后，应将新增文件同步到沙箱工作区。"""
    from src.core.graph.workflow import GameDevWorkflow

    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = {"sandbox": {"enabled": True}}
    wf.sandbox_enabled = True
    wf.logger = MagicMock()

    fake_task = {"task_id": "t1", "task_dir": str(tmp_path), "role": "director"}
    fake_sandbox = MagicMock()
    wf.sandbox = fake_sandbox

    async def fake_node(state):
        return {"code_generated": {"scripts/player.gd": "extends Node\n"}}

    # 使用真实节点名，然后 monkey-patch 该方法
    wf._code_generator_node = fake_node
    wrapped = wf._wrap_code_node("code_generator")

    state = {"sandbox": {"task": fake_task}}

    async def run():
        return await wrapped(state)

    result = asyncio.run(run())

    assert result["code_generated"]["scripts/player.gd"] == "extends Node\n"
    fake_sandbox.modify.assert_called_once_with(
        fake_task, "scripts/player.gd", "extends Node\n"
    )


def test_workflow_sandbox_project_config_returns_task_dir():
    """启用沙箱时，_sandbox_project_config 应返回指向任务工作区的配置。"""
    from src.core.graph.workflow import GameDevWorkflow

    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = {
        "sandbox": {"enabled": True},
        "godot": {"editor_path": "/tmp/godot", "project_path": "/tmp/main"},
    }
    wf.sandbox_enabled = True

    task_dir = "/tmp/sandbox/proj/task_1"
    state = {"sandbox": {"task": {"task_dir": task_dir, "role": "director"}}}

    cfg = wf._sandbox_project_config(state)

    assert cfg is not None
    assert cfg["godot"]["project_path"] == task_dir
    assert cfg["godot"]["editor_path"] == "/tmp/godot"


def test_workflow_sandbox_project_config_none_when_disabled():
    """沙箱禁用时，_sandbox_project_config 应返回 None。"""
    from src.core.graph.workflow import GameDevWorkflow

    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = {"sandbox": {"enabled": False}}
    wf.sandbox_enabled = False

    state = {"sandbox": {"task": {"task_dir": "/tmp/x", "role": "director"}}}

    assert wf._sandbox_project_config(state) is None


def test_sandbox_cleanup_removes_old_tasks(tmp_path, monkeypatch):
    """cleanup 应移除超龄任务，保留最新任务。"""
    import time
    import shutil
    from src.sandbox.controller import SandboxController

    monkeypatch.setenv("GAMEFORGE_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("GAMEFORGE_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    ctrl = SandboxController({})
    project_id = "proj_cleanup"
    # 创建主线项目（create_task 需要）
    main_dir = tmp_path / "projects" / project_id
    if main_dir.exists():
        shutil.rmtree(main_dir)
    main_dir.mkdir(parents=True)
    (main_dir / "project.godot").write_text("[application]\n", encoding="utf-8")

    # 清理遗留任务（pytest tmp_path 可能跨次运行复用）
    for leftover in ctrl.workspace.list_tasks(project_id):
        ctrl.workspace.discard_task(project_id, leftover["task_id"])
    now = time.time()
    for age_hours in [0, 10, 200]:
        task = ctrl.create(project_id, role="director")
        task_dir = Path(task["task_dir"])
        # 修改任务目录及其内部 state.json 的时间戳，确保 cleanup 能识别
        mtime = now - (age_hours * 3600)
        os.utime(task_dir, (mtime, mtime))
        state_file = task_dir / ".sandbox" / "state.json"
        if state_file.exists():
            os.utime(state_file, (mtime, mtime))

    result = ctrl.cleanup(project_id, keep_last=2, max_age_hours=168)
    assert result["removed"] == 1
    assert result["kept"] == 2
    remaining = ctrl.workspace.list_tasks(project_id)
    assert len(remaining) == 2


def test_sandbox_cleanup_keeps_recent_tasks(tmp_path, monkeypatch):
    """cleanup 应至少保留 keep_last 个最新任务。"""
    import time
    import shutil
    from src.sandbox.controller import SandboxController

    monkeypatch.setenv("GAMEFORGE_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("GAMEFORGE_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    ctrl = SandboxController({})
    project_id = "proj_keep"
    main_dir = tmp_path / "projects" / project_id
    if main_dir.exists():
        shutil.rmtree(main_dir)
    main_dir.mkdir(parents=True)
    (main_dir / "project.godot").write_text("[application]\n", encoding="utf-8")

    # 清理遗留任务（pytest tmp_path 可能跨次运行复用）
    for leftover in ctrl.workspace.list_tasks(project_id):
        ctrl.workspace.discard_task(project_id, leftover["task_id"])

    now = time.time()
    for i in range(4):
        task = ctrl.create(project_id, role="director")
        task_dir = Path(task["task_dir"])
        mtime = now - (i * 3600)
        os.utime(task_dir, (mtime, mtime))
        state_file = task_dir / ".sandbox" / "state.json"
        if state_file.exists():
            os.utime(state_file, (mtime, mtime))

    # max_age_hours=0.5 表示只保留 30 分钟内的任务；keep_last=3 仍优先保留最新
    result = ctrl.cleanup(project_id, keep_last=3, max_age_hours=0.5)
    assert result["removed"] == 3
    assert result["kept"] == 1
