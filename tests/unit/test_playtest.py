"""playtest 运行器测试（输入回放 + 证据评分）。

不依赖真实 Godot：评分、注入、跳过逻辑全部可离线验证；
真实引擎跑通由 available() 检查 + skip_when_unavailable 兜底。
"""

import pytest

import src.core.paths as paths
from src.engine.godot.playtest import (
    REPORT_SCHEMA,
    PlaytestRunner,
    build_default_action_plan,
    evaluate_report,
)


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    monkeypatch.setattr(paths, "PROJECTS_ROOT", projects)
    r = PlaytestRunner({
        "godot": {"editor_path": str(tmp_path / "no_godot"), "project_path": str(projects / "p1")},
        "playtest": {"skip_when_unavailable": True},
    })
    return r


class TestEvaluateReport:
    def test_passing_report(self):
        report = {
            "schema": REPORT_SCHEMA,
            "ok": True,
            "actions_executed": 10,
            "frames_captured": 12,
        }
        verdict = evaluate_report(report, console_errors=[])
        assert verdict["ok"] is True
        assert verdict["checks"]["actions_executed"] is True
        assert verdict["checks"]["frames_captured"] is True

    def test_missing_report_fails(self):
        verdict = evaluate_report(None, console_errors=[])
        assert verdict["ok"] is False
        assert "report_missing_or_invalid" in verdict["errors"]

    def test_no_actions_executed_fails(self):
        report = {"schema": REPORT_SCHEMA, "ok": False, "actions_executed": 0, "frames_captured": 0}
        verdict = evaluate_report(report, console_errors=[])
        assert verdict["ok"] is False
        assert "no_actions_executed" in verdict["errors"]

    def test_console_errors_over_budget(self):
        report = {"schema": REPORT_SCHEMA, "ok": True, "actions_executed": 5, "frames_captured": 3}
        errs = [{"pattern": "SCRIPT ERROR"}, {"pattern": "SCRIPT ERROR"}]
        verdict = evaluate_report(report, console_errors=errs, max_console_errors=1)
        assert verdict["ok"] is False
        assert verdict["checks"]["console_errors"] is False

    def test_require_frames_flag(self):
        report = {"schema": REPORT_SCHEMA, "ok": True, "actions_executed": 5, "frames_captured": 0}
        assert evaluate_report(report, console_errors=[], require_frames=True)["ok"] is False
        assert evaluate_report(report, console_errors=[], require_frames=False)["ok"] is True

    def test_wrong_schema_fails(self):
        report = {"schema": "other.v1", "ok": True, "actions_executed": 5, "frames_captured": 3}
        verdict = evaluate_report(report, console_errors=[])
        assert verdict["ok"] is False
        assert verdict["checks"]["schema"] is False


class TestActionPlan:
    def test_default_plan_uses_builtin_actions(self):
        plan = build_default_action_plan()
        assert plan, "动作脚本不应为空"
        keys = {a["key"] for a in plan if a["type"] == "key"}
        # 内置 ui_* 动作由方向键/空格触发，任何 Godot 项目可用
        assert {"Right", "Left", "Space"} <= keys
        # 时间轴单调
        ts = [a["t"] for a in plan]
        assert ts == sorted(ts)
        # 所有按下都有对应释放（不残留按键状态）
        for a in plan:
            if a["type"] == "key" and a["pressed"]:
                releases = [b for b in plan if b["type"] == "key" and not b["pressed"] and b["key"] == a["key"]]
                assert releases, f"{a['key']} 的按下没有对应释放"


class TestRunnerSkip:
    def test_skips_without_godot(self, runner):
        result = runner.run(build_default_action_plan(), project_id="p1")
        assert result["skipped"] is True
        assert result["ok"] is False
        assert "skip_reason" in result

    def test_raises_when_no_skip(self, tmp_path):
        r = PlaytestRunner({
            "godot": {"editor_path": str(tmp_path / "no_godot"), "project_path": str(tmp_path / "proj")},
            "playtest": {"skip_when_unavailable": False},
        })
        result = r.run(build_default_action_plan(), project_id="p1")
        assert result["skipped"] is False
        assert result["ok"] is False
        assert any("环境不可用" in f for f in result["findings"])


class TestRecorderInjection:
    def _make_project(self, tmp_path, with_autoload_section=False, preinject=False):
        proj = tmp_path / "proj"
        (proj / "addons" / "gameforge").mkdir(parents=True, exist_ok=True)
        pg = "[autoload]\nGameForgePreviewRunner=\"*res://addons/gameforge/preview_runner.gd\"\n" if with_autoload_section \
            else "[application]\nconfig_version=5\n"
        (proj / "project.godot").write_text(pg, encoding="utf-8")
        if preinject:
            (proj / "project.godot").write_text(pg + 'GameForgePlaytestRecorder="*res://addons/gameforge/playtest_recorder.gd"\n', encoding="utf-8")
        return proj

    def test_inject_copies_script_and_registers_autoload(self, tmp_path, monkeypatch):

        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        proj = self._make_project(tmp_path, with_autoload_section=True)
        r = PlaytestRunner({"godot": {"editor_path": "", "project_path": str(proj)}})
        err = r._inject_recorder(str(proj))
        assert err == ""
        assert (proj / "addons" / "gameforge" / "playtest_recorder.gd").is_file()
        text = (proj / "project.godot").read_text(encoding="utf-8")
        assert 'GameForgePlaytestRecorder="*res://addons/gameforge/playtest_recorder.gd"' in text
        # 注入位置紧跟 [autoload] 段
        assert text.index("GameForgePlaytestRecorder") > text.index("[autoload]")

    def test_inject_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        proj = self._make_project(tmp_path, preinject=True)
        r = PlaytestRunner({"godot": {"editor_path": "", "project_path": str(proj)}})
        before = (proj / "project.godot").read_text(encoding="utf-8")
        assert r._inject_recorder(str(proj)) == ""
        assert (proj / "project.godot").read_text(encoding="utf-8") == before

    def test_inject_creates_autoload_section_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        proj = self._make_project(tmp_path, with_autoload_section=False)
        r = PlaytestRunner({"godot": {"editor_path": "", "project_path": str(proj)}})
        assert r._inject_recorder(str(proj)) == ""
        text = (proj / "project.godot").read_text(encoding="utf-8")
        assert "[autoload]" in text and "GameForgePlaytestRecorder" in text

    def test_inject_reports_missing_project_godot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        proj = tmp_path / "empty_proj"
        proj.mkdir(exist_ok=True)
        r = PlaytestRunner({"godot": {"editor_path": "", "project_path": str(proj)}})
        assert "project.godot" in r._inject_recorder(str(proj))


class TestPathsIntegration:
    def test_artifacts_addressed_via_paths(self, runner, tmp_path):
        """产物必须落在 paths.playtest_dir 约定位置。"""
        projects = tmp_path / "projects"
        assert paths.playtest_dir("p1", "r9") == \
            projects / "p1" / ".gameforge" / "r9" / "playtest"
        assert paths.playtest_actions_path("p1", "r9").name == "actions.json"
        assert paths.playtest_report_path("p1", "r9").name == "report.json"
