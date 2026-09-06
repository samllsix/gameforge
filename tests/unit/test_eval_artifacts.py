"""产物级评测测试（src/eval/artifacts.py）。

评测只读已有产物、写 summary.json——不生成、不运行游戏。
用 fixture 产物组合验证各指标与聚合逻辑。
"""
import shutil

import pytest

import src.core.paths as paths
from src.eval.artifacts import (
    EVAL_SUMMARY_SCHEMA,
    evaluate_run,
)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """一个带基本骨架的生成项目。"""
    projects = tmp_path / "projects"
    shutil.rmtree(projects, ignore_errors=True)
    monkeypatch.setattr(paths, "PROJECTS_ROOT", projects)
    pid = "p1"
    (paths.ensure_project_dir(pid) / "project.godot").write_text("[application]\n", encoding="utf-8")
    scenes = paths.project_dir(pid) / "scenes"
    scenes.mkdir()
    (scenes / "Main.tscn").write_text("[gd_scene]\n", encoding="utf-8")
    (paths.project_dir(pid) / "scripts").mkdir()
    (paths.project_dir(pid) / "scripts" / "player.gd").write_text("extends Node2D\n", encoding="utf-8")
    paths.write_json(paths.scene_ir_path(pid), {"project_id": pid, "scene_ir": {}})
    return pid


def _write_run_artifacts(pid: str, run_id: str, *, smoke=None, playtest=None, review=None):
    run_dir = paths.run_dir(pid, run_id)
    if smoke is not None:
        paths.write_json(run_dir / "runtime_smoke.json", smoke)
    if playtest is not None:
        paths.write_json(run_dir / "playtest" / "report.json", playtest)
    if review is not None:
        paths.write_json(run_dir / "visual_review.json", review)


GOOD_PLAYTEST = {
    "schema": "gameforge.playtest_report.v1",
    "ok": True,
    "actions_executed": 10,
    "frames_captured": 12,
}


class TestEvaluateRun:
    def test_full_pass_run_scores_high(self, project):
        _write_run_artifacts(project, "r1",
                             smoke={"runnable": True, "errors": []},
                             playtest=GOOD_PLAYTEST,
                             review={"ok": True, "skipped": False, "issues": [], "summary": "正常"})
        summary = evaluate_run(project, "r1")
        assert summary["schema"] == EVAL_SUMMARY_SCHEMA
        assert summary["overall_score"] == 100.0
        by_name = {m["name"]: m for m in summary["metrics"]}
        assert all(not m["skipped"] for m in summary["metrics"])
        assert by_name["playtest"]["weight"] == 2.0  # 试玩权重更高
        # summary 落盘
        saved = paths.read_json(paths.eval_summary_path(project, "r1"))
        assert saved["overall_score"] == 100.0

    def test_missing_evidence_is_skipped_not_failed(self, project):
        """没有任何 run 产物时：只有完整性参与计分，其余 skipped。"""
        summary = evaluate_run(project, "r1")
        by_name = {m["name"]: m for m in summary["metrics"]}
        assert by_name["runtime_smoke"]["skipped"] is True
        assert by_name["playtest"]["skipped"] is True
        assert by_name["visual_review"]["skipped"] is True
        # 完整性满分 → 总分 100（不因证据缺失而扣分）
        assert summary["overall_score"] == 100.0

    def test_playtest_failure_caps_score(self, project):
        _write_run_artifacts(project, "r1", smoke={"runnable": True},
                             playtest={"schema": "gameforge.playtest_report.v1",
                                       "ok": False, "actions_executed": 0, "frames_captured": 0})
        summary = evaluate_run(project, "r1")
        assert summary["overall_score"] < 100.0
        by_name = {m["name"]: m for m in summary["metrics"]}
        assert by_name["playtest"]["value"] == 0.0

    def test_high_severity_visual_review_fails(self, project):
        _write_run_artifacts(project, "r1",
                             smoke={"runnable": True}, playtest=GOOD_PLAYTEST,
                             review={"ok": False, "skipped": False,
                                     "issues": [{"severity": "high", "area": "render"}],
                                     "summary": "黑屏"})
        summary = evaluate_run(project, "r1")
        by_name = {m["name"]: m for m in summary["metrics"]}
        assert by_name["visual_review"]["value"] == 0.0
        assert summary["overall_score"] < 100.0

    def test_skipped_visual_review_excluded(self, project):
        _write_run_artifacts(project, "r1",
                             smoke={"runnable": True}, playtest=GOOD_PLAYTEST,
                             review={"ok": False, "skipped": True, "reason": "no_key"})
        summary = evaluate_run(project, "r1")
        by_name = {m["name"]: m for m in summary["metrics"]}
        assert by_name["visual_review"]["skipped"] is True
        assert summary["overall_score"] == 100.0

    def test_default_run_id_resolution(self, project):
        """不传 run_id 时取最近一次时间戳 run。"""
        _write_run_artifacts(project, "20260101_000000", smoke={"runnable": True})
        _write_run_artifacts(project, "20260202_000000", smoke={"runnable": True})
        summary = evaluate_run(project)
        assert summary["run_id"] == "20260202_000000"

    def test_missing_project_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        import pytest as _pytest

        with _pytest.raises(FileNotFoundError):
            evaluate_run("no_such")
