"""GameForge - 产物级评测（GameFactory-3A 风格 eval.py）。

只读取已存在的产物并评分，绝不重新生成、绝不重新运行游戏：

    projects/<pid>/
    ├── project.godot ...            → 完整性检查
    ├── .scene_ir.json               → 场景 IR 存在性
    └── .gameforge/<run_id>/
        ├── runtime_smoke.json       → 可运行性
        ├── playtest/report.json     → 试玩执行证据
        └── visual_review.json       → 视觉审查结论

结果写 ``.gameforge/<run_id>/eval/summary.json``（schema
``gameforge.eval_summary.v1``）。缺失的产物记为 skipped，
不参与总分（评测端容忍证据不全，但不会假装通过）。

用法::

    from src.eval.artifacts import evaluate_run
    summary = evaluate_run("gf_xxx")          # 用最近一次 run
    summary = evaluate_run("gf_xxx", "20260906_120000")
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from src.core import paths

logger = structlog.get_logger()

EVAL_SUMMARY_SCHEMA = "gameforge.eval_summary.v1"


def _metric(name: str, value: float, *, weight: float, skipped: bool = False,
            detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "value": round(value, 1),
        "weight": weight,
        "skipped": skipped,
        "detail": detail or {},
    }


def _eval_project_completeness(project_path) -> Dict[str, Any]:
    """产物完整性：项目骨架 + 场景 + 脚本 + IR。"""
    checks = {
        "project_godot": (project_path / "project.godot").is_file(),
        "has_scene": any(project_path.rglob("*.tscn")),
        "has_script": any(project_path.rglob("*.gd")),
        "has_scene_ir": (project_path / paths.SCENE_IR_FILENAME).is_file(),
    }
    score = 100.0 * sum(checks.values()) / len(checks)
    return _metric("project_completeness", score, weight=1.0, detail=checks)


def _eval_runtime_smoke(run_dir) -> Dict[str, Any]:
    """可运行性：runtime_smoke.json 的 runnable 结论。"""
    data = paths.read_json(run_dir / "runtime_smoke.json")
    if data is None:
        return _metric("runtime_smoke", 0.0, weight=1.0, skipped=True,
                       detail={"reason": "evidence_missing"})
    runnable = data.get("runnable")
    if runnable is None:
        return _metric("runtime_smoke", 0.0, weight=1.0, skipped=True,
                       detail={"reason": "smoke_skipped"})
    return _metric("runtime_smoke", 100.0 if runnable else 0.0, weight=1.0,
                   detail={"runnable": runnable})


def _eval_playtest(run_dir) -> Dict[str, Any]:
    """试玩证据：report.json 的执行与评分结论。"""
    report = paths.read_json(run_dir / "playtest" / "report.json")
    if report is None:
        return _metric("playtest", 0.0, weight=2.0, skipped=True,
                       detail={"reason": "evidence_missing"})
    checks = {
        "actions_executed": int(report.get("actions_executed") or 0) > 0,
        "frames_captured": int(report.get("frames_captured") or 0) > 0,
        "recorder_ok": report.get("ok") is True,
    }
    # 部分通过：动作执行了但整体未通过（如超时/有报错）给部分分
    score = 100.0 if all(checks.values()) else (40.0 if checks["actions_executed"] else 0.0)
    return _metric("playtest", score, weight=2.0, detail={"checks": checks})


def _eval_visual_review(run_dir) -> Dict[str, Any]:
    """视觉审查结论；skipped/缺失不参与总分。"""
    review = paths.read_json(run_dir / "visual_review.json")
    if review is None:
        return _metric("visual_review", 0.0, weight=1.0, skipped=True,
                       detail={"reason": "evidence_missing"})
    if review.get("skipped"):
        return _metric("visual_review", 0.0, weight=1.0, skipped=True,
                       detail={"reason": review.get("reason", "skipped")})
    return _metric("visual_review", 100.0 if review.get("ok") else 0.0, weight=1.0,
                   detail={"summary": review.get("summary", ""),
                           "issue_count": len(review.get("issues") or [])})


def evaluate_run(project_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
    """评测一次 run 的全部产物，写 eval/summary.json 并返回。

    Args:
        project_id: 项目 id
        run_id: run id；缺省取最近一次 run

    Returns:
        summary 字典（与落盘内容一致）；项目不存在时抛 FileNotFoundError。
    """
    project_path = paths.resolve_project(project_id)
    if run_id is None:
        run_id = paths.latest_run_id(project_id)
        if run_id is None:
            run_id = paths.DEFAULT_RUN_ID
    run_dir = paths.run_dir(project_id, run_id)

    metrics: List[Dict[str, Any]] = [
        _eval_project_completeness(project_path),
        _eval_runtime_smoke(run_dir),
        _eval_playtest(run_dir),
        _eval_visual_review(run_dir),
    ]
    counted = [m for m in metrics if not m["skipped"]]
    if counted:
        total_weight = sum(m["weight"] for m in counted)
        overall = sum(m["value"] * m["weight"] for m in counted) / total_weight
    else:
        overall = 0.0

    summary: Dict[str, Any] = {
        "schema": EVAL_SUMMARY_SCHEMA,
        "project_id": project_id,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(),
        "overall_score": round(overall, 1),
        "metrics": metrics,
    }
    paths.write_json(paths.eval_summary_path(project_id, run_id), summary)
    logger.info("eval.summary_written", project_id=project_id, run_id=run_id,
                overall=summary["overall_score"])
    return summary
