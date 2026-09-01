"""快照与回滚：AI 开发一定失败，任何修改前留痕。

设计（文件级快照，零外部依赖，游戏项目通常仅数 MB）：

  workspace/<project_id>/snapshots/
    <snap_id>/
      manifest.json       # 时间/触发者/文件清单+SHA256
      files/<原相对路径>   # 快照文件本体

用法：
  snap.create(task_dir)          # 全量快照（修改前）
  snap.snapshot_file(task_dir, rel_path)  # 单文件快照（modify 前自动调用）
  snap.rollback(task_dir, snap_id)        # 整体回滚
  snap.rollback_file(task_dir, rel_path, snap_id)  # 单文件回滚
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

SKIP_DIRS = {"export", ".godot", "__pycache__", ".git", ".import"}
MAX_SNAPSHOTS_PER_TASK = 10  # 超出时淘汰最旧的非 pinned 快照


class SnapshotError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SnapshotManager:
    def __init__(self, workspace_root: Optional[str] = None):
        from src.sandbox.workspace import _repo_root

        self.root = Path(workspace_root or os.environ.get("GAMEFORGE_WORKSPACE_ROOT", Path(_repo_root()) / "workspace"))

    # ── 路径解析 ──
    def _snap_root(self, project_id: str, task_id: str) -> Path:
        return self.root / project_id / "snapshots" / task_id

    @staticmethod
    def _ids(task_dir: Path) -> tuple:
        """从任务工作区路径反解 (project_id, task_id)。"""
        # .../workspace/<project_id>/tasks/<task_id>
        return task_dir.parts[-3], task_dir.parts[-1]

    # ── 快照 ──
    def create(self, task_dir: str | Path, label: str = "", pinned: bool = False) -> str:
        """全量快照任务工作区，返回 snap_id。"""
        task_dir = Path(task_dir)
        if not task_dir.is_dir():
            raise SnapshotError(f"任务工作区不存在: {task_dir}")
        project_id, task_id = self._ids(task_dir)

        snap_id = f"snap_{int(time.time())}_{os.urandom(2).hex()}"
        snap_dir = self._snap_root(project_id, task_id) / snap_id
        files_dir = snap_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        entries = []
        for src in sorted(task_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(task_dir)
            if any(part in SKIP_DIRS for part in rel.parts) or rel.name.endswith(".import"):
                continue
            dst = files_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            entries.append({"path": rel.as_posix(), "sha256": _sha256(src), "size": src.stat().st_size})

        manifest = {
            "snap_id": snap_id,
            "task_id": task_id,
            "project_id": project_id,
            "label": label,
            "pinned": pinned,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file_count": len(entries),
            "files": entries,
        }
        (snap_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        self._prune(project_id, task_id)
        logger.info("sandbox.snapshot_created", snap_id=snap_id, files=len(entries), label=label)
        return snap_id

    def snapshot_file(self, task_dir: str | Path, rel_path: str) -> Optional[str]:
        """单文件快照（modify 前自动调用）。文件不存在返回 None。"""
        task_dir = Path(task_dir)
        src = (task_dir / rel_path).resolve()
        if not str(src).startswith(str(task_dir.resolve())):
            raise SnapshotError(f"路径越界: {rel_path}")
        if not src.is_file():
            return None

        project_id, task_id = self._ids(task_dir)
        snap_id = f"file_{int(time.time())}_{os.urandom(2).hex()}"
        snap_dir = self._snap_root(project_id, task_id) / snap_id / "files"
        dst = snap_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        (self._snap_root(project_id, task_id) / snap_id / "manifest.json").write_text(
            json.dumps(
                {
                    "snap_id": snap_id,
                    "task_id": task_id,
                    "project_id": project_id,
                    "single_file": rel_path,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return snap_id

    # ── 回滚 ──
    def rollback(self, task_dir: str | Path, snap_id: str) -> int:
        """整工作区回滚到指定快照，返回恢复文件数。"""
        task_dir = Path(task_dir)
        project_id, task_id = self._ids(task_dir)
        snap_dir = self._snap_root(project_id, task_id) / snap_id
        manifest = self._load_manifest(snap_dir)

        restored = 0
        files_dir = snap_dir / "files"
        for entry in manifest["files"]:
            src = files_dir / entry["path"]
            if not src.is_file():
                continue
            dst = task_dir / entry["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored += 1
        logger.info("sandbox.snapshot_rolled_back", snap_id=snap_id, restored=restored)
        return restored

    def rollback_file(self, task_dir: str | Path, rel_path: str, snap_id: Optional[str] = None) -> bool:
        """单文件回滚。snap_id 缺省取包含该文件的最近快照。"""
        task_dir = Path(task_dir)
        project_id, task_id = self._ids(task_dir)

        candidates = []
        sroot = self._snap_root(project_id, task_id)
        if sroot.is_dir():
            for d in sorted(sroot.iterdir(), reverse=True):
                if snap_id and d.name != snap_id:
                    continue
                mf = self._load_manifest(d)
                if mf is None:
                    continue
                if mf.get("single_file"):
                    if mf["single_file"] == rel_path:
                        candidates.append((d.name, d / "files" / rel_path))
                else:
                    f = d / "files" / rel_path
                    if f.is_file():
                        candidates.append((d.name, f))
        for name, src in candidates:
            if src.is_file():
                dst = task_dir / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                logger.info("sandbox.file_rolled_back", path=rel_path, snap_id=name)
                return True
        return False

    # ── 查询 ──
    def list_snapshots(self, task_dir: str | Path) -> List[Dict[str, object]]:
        task_dir = Path(task_dir)
        project_id, task_id = self._ids(task_dir)
        sroot = self._snap_root(project_id, task_id)
        if not sroot.is_dir():
            return []
        out = []
        for d in sorted(sroot.iterdir(), reverse=True):
            mf = self._load_manifest(d)
            if mf:
                out.append(
                    {
                        "snap_id": mf.get("snap_id", d.name),
                        "label": mf.get("label", ""),
                        "single_file": mf.get("single_file"),
                        "created_at": mf.get("created_at", ""),
                        "file_count": mf.get("file_count", 0),
                        "pinned": mf.get("pinned", False),
                    }
                )
        return out

    # ── 内部 ──
    @staticmethod
    def _load_manifest(snap_dir: Path) -> Optional[Dict]:
        mf = snap_dir / "manifest.json"
        if not mf.is_file():
            return None
        try:
            return json.loads(mf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _prune(self, project_id: str, task_id: str) -> None:
        """快照数量超限时淘汰最旧非 pinned 全量快照。"""
        sroot = self._snap_root(project_id, task_id)
        if not sroot.is_dir():
            return
        full_snaps = []
        for d in sroot.iterdir():
            mf = self._load_manifest(d)
            if mf and not mf.get("single_file") and not mf.get("pinned"):
                full_snaps.append(d)
        full_snaps.sort(key=lambda p: p.name)
        excess = len(full_snaps) - MAX_SNAPSHOTS_PER_TASK
        for d in full_snaps[: max(excess, 0)]:
            shutil.rmtree(d, ignore_errors=True)
