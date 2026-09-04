"""任务级工作区管理。

结构（WORKSPACE_ROOT 默认 <repo>/workspace，gitignore 排除）：

  workspace/
    <project_id>/
      tasks/
        <task_id>/        # 任务工作区：从主线 projects/<project_id>/ 复制
      snapshots/          # 该项目全部快照（见 snapshot.py）
      state.json          # 项目沙箱元数据

工作区生命周期：create（从主线复制）→ agent 修改 → 测试 →
merge（合并回主线）/ discard（丢弃）。多 Agent 并行 = 多任务工作区互不干扰。
"""

import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# 复制时跳过的目录：导出产物与生成素材不入沙箱（体积大且可再生）
SKIP_DIRS = {"export", ".godot", "__pycache__", ".git"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class WorkspaceError(RuntimeError):
    pass


class WorkspaceManager:
    def __init__(self, workspace_root: Optional[str] = None, projects_root: Optional[str] = None):
        self.root = Path(workspace_root or os.environ.get("GAMEFORGE_WORKSPACE_ROOT", _repo_root() / "workspace"))
        self.projects_root = Path(projects_root or os.environ.get("GAMEFORGE_PROJECTS_ROOT", _repo_root() / "projects"))
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 主线（projects/<id>）──
    def main_project(self, project_id: str) -> Path:
        self._validate_id(project_id)
        return self.projects_root / project_id

    def _validate_id(self, project_id: str) -> None:
        if not project_id or ".." in project_id or "/" in project_id.replace("\\", "/") or project_id.startswith("."):
            raise WorkspaceError(f"非法 project_id: {project_id!r}")

    def _validate_task(self, task_id: str) -> None:
        if not task_id or ".." in task_id or "/" in task_id.replace("\\", "/") or task_id.startswith("."):
            raise WorkspaceError(f"非法 task_id: {task_id!r}")

    # ── 任务工作区 ──
    def task_path(self, project_id: str, task_id: str) -> Path:
        self._validate_id(project_id)
        self._validate_task(task_id)
        return self.root / project_id / "tasks" / task_id

    def create_task(self, project_id: str, task_id: Optional[str] = None) -> Path:
        """从主线复制出任务工作区，返回其路径。

        task_id 缺省自动生成 task_<UTC时间戳>_<短随机>。
        主线不存在时抛 WorkspaceError（fail-closed，不隐式建空项目）。
        """
        main = self.main_project(project_id)
        if not main.is_dir():
            raise WorkspaceError(f"主线项目不存在: {main}")

        task_id = task_id or f"task_{int(time.time())}_{os.urandom(2).hex()}"
        task_dir = self.task_path(project_id, task_id)
        if task_dir.exists():
            raise WorkspaceError(f"任务工作区已存在: {task_dir}")

        shutil.copytree(main, task_dir, ignore=shutil.ignore_patterns(*SKIP_DIRS), dirs_exist_ok=False)
        self._save_state(project_id, task_id, "created")
        logger.info("sandbox.workspace_created", project_id=project_id, task_id=task_id)
        return task_dir

    def merge_task(self, project_id: str, task_id: str) -> Path:
        """把任务工作区合并回主线（覆盖同名文件，保留主线独有文件）。"""
        task_dir = self.task_path(project_id, task_id)
        if not task_dir.is_dir():
            raise WorkspaceError(f"任务工作区不存在: {task_dir}")
        main = self.main_project(project_id)

        copied = 0
        for src in task_dir.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(task_dir)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            dst = main / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        self._save_state(project_id, task_id, "merged")
        logger.info("sandbox.workspace_merged", project_id=project_id, task_id=task_id, files=copied)
        return main

    def discard_task(self, project_id: str, task_id: str) -> None:
        """丢弃任务工作区（不碰主线）。"""
        task_dir = self.task_path(project_id, task_id)
        if task_dir.is_dir():
            shutil.rmtree(task_dir, ignore_errors=True)
        self._save_state(project_id, task_id, "discarded")
        logger.info("sandbox.workspace_discarded", project_id=project_id, task_id=task_id)

    def list_tasks(self, project_id: str) -> List[Dict[str, str]]:
        tasks_dir = self.root / project_id / "tasks"
        if not tasks_dir.is_dir():
            return []
        out = []
        for d in sorted(tasks_dir.iterdir()):
            if d.is_dir():
                out.append({"task_id": d.name, "status": self._task_status(project_id, d.name)})
        return out

    def destroy_project(self, project_id: str) -> None:
        """清理该项目的全部沙箱数据（工作区+快照），不动主线。"""
        pdir = self.root / project_id
        if pdir.is_dir():
            shutil.rmtree(pdir, ignore_errors=True)
        logger.info("sandbox.project_destroyed", project_id=project_id)

    # ── 元数据 ──
    def _state_file(self, project_id: str) -> Path:
        return self.root / project_id / "state.json"

    def _save_state(self, project_id: str, task_id: str, status: str) -> None:
        sf = self._state_file(project_id)
        state: Dict[str, Dict[str, str]] = {}
        if sf.is_file():
            try:
                state = json.loads(sf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                state = {}
        state.setdefault(task_id, {})
        state[task_id]["status"] = status
        state[task_id]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    def _task_status(self, project_id: str, task_id: str) -> str:
        sf = self._state_file(project_id)
        if not sf.is_file():
            return "unknown"
        try:
            return json.loads(sf.read_text(encoding="utf-8")).get(task_id, {}).get("status", "unknown")
        except (json.JSONDecodeError, OSError):
            return "unknown"
