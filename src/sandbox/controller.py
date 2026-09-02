"""SandboxController — 沙箱平台统一入口。

Agent 不再直接 write_file 到项目，而是：

    sandbox = SandboxController(config)
    task = sandbox.create(project_id, role="code_agent")
    sandbox.modify(task, "scripts/player.gd", new_code)     # 权限检查+单文件快照
    result = sandbox.execute(task, [godot, "--headless", ...])  # 资源笼执行
    if result.success:
        sandbox.merge(task)                                  # 测试通过合并主线
    else:
        sandbox.rollback(task)                               # 失败回滚

生命周期：create → modify/execute → merge | rollback → destroy
"""

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from src.sandbox.monitor import sample_process
from src.sandbox.policy.permission import (
    ROLES,
    PermissionDenied,
    RolePolicies,
    check_write,
    describe_role,
    resolve_role,
)
from src.sandbox.runtime.process import ExecutionOutcome, RuntimePolicy, run_isolated
from src.sandbox.snapshot import SnapshotManager
from src.sandbox.workspace import WorkspaceManager

logger = structlog.get_logger(__name__)


class SandboxController:
    """统一入口：创建沙箱、权限校验、快照留痕、受限执行、合并/回滚。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = (config or {}).get("sandbox", {})
        self.workspace = WorkspaceManager(
            workspace_root=cfg.get("workspace_root") or os.environ.get("GAMEFORGE_WORKSPACE_ROOT"),
        )
        self.snapshots = SnapshotManager(workspace_root=str(self.workspace.root))
        self._policy_overrides: Dict[str, RuntimePolicy] = {}
        self._default_policy = RuntimePolicy(
            memory_limit_mb=int(cfg.get("memory_limit_mb", 2048)),
            cpu_time_seconds=int(cfg.get("cpu_time_seconds", 120)),
            timeout_seconds=int(cfg.get("timeout_seconds", 120)),
            process_limit=int(cfg.get("process_limit", 32)),
        )
        self._active_runs: Dict[str, Dict[str, Any]] = {}  # task_id → {pid, cmd, started_at}

    # ── 生命周期 ──
    def create(
        self,
        project_id: str,
        role: str = "unknown",
        task_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """创建任务工作区（从主线复制）。返回 {task_id, task_dir, role}。

        role 支持角色名（code_agent）或 Agent 名（CodeGeneratorAgent），自动映射。
        """
        role = role if role in ROLES else resolve_role(role)
        task_dir = self.workspace.create_task(project_id, task_id)
        tid = task_dir.name
        # 出生快照：任何修改前的干净基线
        self.snapshots.create(task_dir, label="baseline")
        logger.info("sandbox.created", project_id=project_id, task_id=tid, role=role)
        return {"task_id": tid, "task_dir": str(task_dir), "role": role}

    def destroy(self, project_id: str, task_id: Optional[str] = None) -> None:
        """销毁任务工作区（或整个项目的沙箱数据）。"""
        if task_id:
            self.workspace.discard_task(project_id, task_id)
        else:
            self.workspace.destroy_project(project_id)

    # ── 修改（权限 + 快照） ──
    def modify(self, task: Dict[str, str], rel_path: str, content: str) -> str:
        """Agent 修改工作区文件：权限检查 → 单文件快照 → 写入。"""
        role = task["role"]
        check_write(role, rel_path)

        task_dir = Path(task["task_dir"])
        target = (task_dir / rel_path).resolve()
        if not str(target).startswith(str(task_dir.resolve())):
            raise PermissionError(f"路径越界: {rel_path}")

        snap_id = self.snapshots.snapshot_file(task_dir, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logger.info("sandbox.modified", task=task["task_id"], path=rel_path, prev_snap=snap_id)
        return snap_id or ""

    def delete_file(self, task: Dict[str, str], rel_path: str) -> bool:
        """删除工作区文件（先快照后删，可回滚）。"""
        check_write(task["role"], rel_path)
        task_dir = Path(task["task_dir"])
        target = (task_dir / rel_path).resolve()
        if not str(target).startswith(str(task_dir.resolve())):
            raise PermissionError(f"路径越界: {rel_path}")
        if not target.is_file():
            return False
        self.snapshots.snapshot_file(task_dir, rel_path)
        target.unlink()
        return True

    # ── 受限执行 ──
    def execute(
        self,
        task: Dict[str, str],
        cmd: List[str],
        policy: Optional[RuntimePolicy] = None,
        timeout: Optional[int] = None,
    ) -> ExecutionOutcome:
        """在资源笼里执行命令（cwd 锁定任务工作区）。

        QA/只读角色也可以执行测试命令——执行不修改文件。
        """
        pol = policy or self._policy_overrides.get(task["task_id"]) or self._default_policy
        if timeout:
            pol.timeout_seconds = timeout
        task_dir = task["task_dir"]

        self._active_runs[task["task_id"]] = {
            "cmd": cmd[:3],
            "started_at": time.strftime("%H:%M:%S"),
        }
        try:
            return run_isolated(cmd, policy=pol, cwd=task_dir)
        finally:
            self._active_runs.pop(task["task_id"], None)

    def set_policy(self, task: Dict[str, str], **overrides) -> RuntimePolicy:
        """给任务设置运行策略（如 QA 跑大场景放宽内存）。"""
        pol = self._policy_overrides.get(task["task_id"], self._default_policy)
        new = RuntimePolicy(
            memory_limit_mb=overrides.get("memory_limit_mb", pol.memory_limit_mb),
            cpu_time_seconds=overrides.get("cpu_time_seconds", pol.cpu_time_seconds),
            timeout_seconds=overrides.get("timeout_seconds", pol.timeout_seconds),
            process_limit=overrides.get("process_limit", pol.process_limit),
        )
        self._policy_overrides[task["task_id"]] = new
        return new

    # ── 快照 / 回滚 ──
    def snapshot(self, task: Dict[str, str], label: str = "") -> str:
        return self.snapshots.create(task["task_dir"], label=label)

    def rollback(self, task: Dict[str, str], snap_id: Optional[str] = None) -> int:
        """回滚工作区。snap_id 缺省回滚到出生基线。"""
        task_dir = task["task_dir"]
        if snap_id:
            return self.snapshots.rollback(task_dir, snap_id)
        for s in self.snapshots.list_snapshots(task_dir):
            if s.get("label") == "baseline":
                return self.snapshots.rollback(task_dir, str(s["snap_id"]))
        raise ValueError("找不到 baseline 快照（不应发生：create 时强制创建）")

    def rollback_file(self, task: Dict[str, str], rel_path: str, snap_id: Optional[str] = None) -> bool:
        return self.snapshots.rollback_file(task["task_dir"], rel_path, snap_id)

    # ── 合并 / 丢弃 ──
    def merge(self, task: Dict[str, str]) -> str:
        """测试通过后合并回主线。合并前自动留快照。"""
        self.snapshots.create(task["task_dir"], label="pre-merge")
        main = self.workspace.merge_task(self._project_of(task), task["task_id"])
        return str(main)

    def discard(self, task: Dict[str, str]) -> None:
        """丢弃任务（改坏了不要了）。"""
        self.workspace.discard_task(self._project_of(task), task["task_id"])

    # ── 状态 ──
    def status(self, project_id: Optional[str] = None, pid: Optional[int] = None) -> Dict[str, Any]:
        """沙箱状态汇总（供前端 Sandbox Center）。"""
        out: Dict[str, Any] = {
            "active_runs": dict(self._active_runs),
            "workspace_root": str(self.workspace.root),
        }
        if pid:
            out["process"] = sample_process(pid)
        if project_id:
            tasks = self.workspace.list_tasks(project_id)
            for t in tasks:
                # 每任务最新快照数
                td = self.workspace.task_path(project_id, t["task_id"])
                t["snapshots"] = self.snapshots.list_snapshots(td)[:3]
            out["project_id"] = project_id
            out["tasks"] = tasks
            out["role_policies"] = {
                r: describe_role(r)
                for r in ("code_agent", "asset_agent", "scene_agent", "qa_agent", "repair_agent")
            }
        return out

    def cleanup(self, project_id: str, keep_last: int = 5, max_age_hours: Optional[int] = 168) -> Dict[str, Any]:
        """清理旧沙箱任务工作区，保留最近 N 个且未超龄的任务。

        - keep_last: 至少保留最近完成/创建的 N 个任务
        - max_age_hours: 超过该小时数的任务直接清理（默认 7 天）
        """
        import time
        tasks = self.workspace.list_tasks(project_id)
        if not tasks:
            return {"removed": 0, "kept": 0, "tasks": []}

        def _mtime(task_dir: Path) -> float:
            try:
                return task_dir.stat().st_mtime
            except OSError:
                return 0.0

        now = time.time()
        cutoff = now - (max_age_hours * 3600) if max_age_hours else None

        # 按修改时间降序排列
        task_dirs = []
        for t in tasks:
            td = self.workspace.task_path(project_id, t["task_id"])
            task_dirs.append((t, td, _mtime(td)))
        task_dirs.sort(key=lambda x: x[2], reverse=True)

        to_remove = []
        to_keep = []
        for idx, (t, td, mtime) in enumerate(task_dirs):
            if cutoff is not None and mtime < cutoff:
                to_remove.append(t["task_id"])
            elif idx >= keep_last:
                to_remove.append(t["task_id"])
            else:
                to_keep.append(t["task_id"])

        for tid in to_remove:
            self.workspace.discard_task(project_id, tid)

        return {"removed": len(to_remove), "kept": len(to_keep), "tasks": to_keep}

    # ── 内部 ──
    @staticmethod
    def _project_of(task: Dict[str, str]) -> str:
        return Path(task["task_dir"]).parts[-3]
