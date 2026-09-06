"""GameForge - 仓库 I/O 路径单一事实源

借鉴 GameFactory-3A 的 paths.py 约定：所有生成产物的路径只在本模块构造，
其他模块一律通过这里的函数取路径。验收约束::

    grep -rn '"projects"' src/ --include="*.py" | grep -v paths.py   # 必须为空

产物编址：生成游戏项目位于 ``projects/<project_id>/``；GameForge 流水线
自身的产物（playtest 录像帧、报告、视觉审查、评测汇总）按
``(project_id, run_id, task_kind, task_id)`` 编址，收在项目内
``.gameforge/``（Godot 导入器忽略点号目录，不污染游戏工程）::

    projects/<project_id>/
    ├── project.godot ...            # 生成的 Godot 游戏
    ├── .scene_ir.json               # Scene IR（历史约定位置，保持不变）
    └── .gameforge/                  # 流水线产物
        └── <run_id>/
            ├── run_meta.json        # 时间 · git sha · 入参摘要
            ├── playtest/
            │   ├── actions.json     # 声明式输入脚本（可重放）
            │   ├── frames/f*.png    # 逐帧截图
            │   └── report.json      # playtest 执行报告
            ├── visual_review.json   # VLM 视觉审查发现
            └── eval/
                └── summary.json     # 本 run 全部指标聚合

本模块零第三方依赖、不导入任何业务模块，可从任何地方（含测试与工具）
安全导入。输出树可通过环境变量整体迁移（指向 scratch 盘等），无需改代码。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# ── 根目录 ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 生成游戏项目的存放根。与 sandbox/workspace.py 共用同一环境变量。
PROJECTS_ROOT = Path(
    os.environ.get("GAMEFORGE_PROJECTS_ROOT", REPO_ROOT / "projects")
).expanduser()

#: 沙箱任务副本、数据库等派生数据的根。
DATA_ROOT = Path(
    os.environ.get("GAMEFORGE_DATA_ROOT", REPO_ROOT / "data")
).expanduser()

#: 流水线产物在项目内的目录名（Godot 忽略点号目录）。
GAMEFORGE_DIR_NAME = ".gameforge"

#: Scene IR 在项目根的历史文件名（API 预览端点依赖，勿改）。
SCENE_IR_FILENAME = ".scene_ir.json"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")

#: run_id 为 "default" 时产物直接落在 .gameforge/default/ 下，随写随覆盖。
DEFAULT_RUN_ID = "default"


# ── 校验 ─────────────────────────────────────────────────────────────────────


def validate_id(external_id: str, *, kind: str = "id") -> str:
    """校验外部传入的 id，拒绝路径穿越与非法字符。

    Args:
        external_id: 项目 id / run id / 任务 id 等外部输入
        kind: 报错时用的名称

    Returns:
        原样返回合法 id

    Raises:
        ValueError: id 为空、含路径分隔符或越界字符时
    """
    if not external_id or not _SAFE_ID_RE.match(str(external_id)):
        raise ValueError(f"非法 {kind}: {external_id!r}")
    return str(external_id)


# ── 生成项目 ─────────────────────────────────────────────────────────────────


def project_dir(project_id: str) -> Path:
    """生成游戏项目的目录（不创建）。"""
    validate_id(project_id, kind="project_id")
    return PROJECTS_ROOT / project_id


def ensure_project_dir(project_id: str) -> Path:
    """生成游戏项目的目录，不存在则创建。"""
    path = project_dir(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_project(project_id: str) -> Path:
    """解析并校验项目路径确实位于 PROJECTS_ROOT 之内（防穿越）。

    Raises:
        ValueError: project_id 非法，或解析后越出 PROJECTS_ROOT。
        FileNotFoundError: 项目目录不存在。
    """
    root = PROJECTS_ROOT.resolve()
    abs_root = (root / project_id).resolve()
    if not abs_root.is_relative_to(root):
        raise ValueError(f"project_id 越出 projects 目录: {project_id!r}")
    if not abs_root.is_dir():
        raise FileNotFoundError(f"项目不存在: {abs_root}")
    return abs_root


def scene_ir_path(project_id: str) -> Path:
    """Scene IR 落盘路径（项目根下，历史约定位置）。"""
    return project_dir(project_id) / SCENE_IR_FILENAME


def sandbox_task_dir(project_id: str, task_id: str) -> Path:
    """沙箱任务工作区路径（data/sandbox/<pid>/tasks/<tid>，不创建）。"""
    validate_id(project_id, kind="project_id")
    validate_id(task_id, kind="task_id")
    return DATA_ROOT / "sandbox" / project_id / "tasks" / task_id


# ── run 编址 ─────────────────────────────────────────────────────────────────


def new_run_id() -> str:
    """生成新的 run id（时间戳，精确到秒）。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def pipeline_root(project_id: str) -> Path:
    """项目内 GameForge 流水线产物根（不创建）。"""
    return project_dir(project_id) / GAMEFORGE_DIR_NAME


def run_dir(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """某一次 run 的产物目录（不创建）。"""
    validate_id(run_id, kind="run_id")
    return pipeline_root(project_id) / run_id


def ensure_run_dir(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """某一次 run 的产物目录，不存在则创建。"""
    path = run_dir(project_id, run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_run_id(project_id: str) -> Optional[str]:
    """最近一次 run 的 id；没有任何 run 时返回 None。

    目录名按时间戳排序即时间序（DEFAULT_RUN_ID 除外，视为最早）。
    """
    root = pipeline_root(project_id)
    if not root.is_dir():
        return None
    run_ids = [
        p.name for p in root.iterdir() if p.is_dir() and p.name != DEFAULT_RUN_ID
    ]
    if not run_ids:
        return DEFAULT_RUN_ID if (root / DEFAULT_RUN_ID).is_dir() else None
    return max(run_ids)


# ── playtest ─────────────────────────────────────────────────────────────────


def playtest_dir(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """playtest 产物目录（actions/frames/report）。"""
    return run_dir(project_id, run_id) / "playtest"


def playtest_actions_path(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """声明式输入脚本路径（注入给游戏内录制器读取）。"""
    return playtest_dir(project_id, run_id) / "actions.json"


def playtest_frames_dir(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """playtest 逐帧截图目录。"""
    return playtest_dir(project_id, run_id) / "frames"


def playtest_report_path(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """playtest 执行报告路径。"""
    return playtest_dir(project_id, run_id) / "report.json"


# ── 视觉审查 / 评测 ──────────────────────────────────────────────────────────


def visual_review_path(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """VLM 视觉审查结果路径。"""
    return run_dir(project_id, run_id) / "visual_review.json"


def eval_dir(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """评测产物目录。"""
    return run_dir(project_id, run_id) / "eval"


def eval_summary_path(project_id: str, run_id: str = DEFAULT_RUN_ID) -> Path:
    """本 run 的评测汇总文件。"""
    return eval_dir(project_id, run_id) / "summary.json"


# ── 落盘工具 ─────────────────────────────────────────────────────────────────


def write_json(path: Path, payload: Any) -> Path:
    """写 JSON 文件（自动建父目录，UTF-8，ensure_ascii=False）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def read_json(path: Path) -> Optional[Any]:
    """读 JSON 文件；不存在或损坏时返回 None（评测端容忍缺失产物）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_run_meta(
    project_id: str,
    run_id: str = DEFAULT_RUN_ID,
    *,
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """写 run_meta.json：记录 run 时间与调用方提供的摘要信息。"""
    payload = {
        "run_id": run_id,
        "project_id": project_id,
        "created_at": datetime.now().isoformat(),
        "gameforge_version": _gameforge_version(),
        **(meta or {}),
    }
    return write_json(run_dir(project_id, run_id) / "run_meta.json", payload)


def _gameforge_version() -> str:
    """读取配置里的版本号，失败不阻塞。"""
    try:
        import yaml  # 延迟导入，保持本模块零强依赖

        cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        return str((cfg or {}).get("app", {}).get("version", "unknown"))
    except Exception:  # noqa: BLE001
        return "unknown"
