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


# ── 空帧检测与渲染模式回退 ───────────────────────────────────────────────────


def _png_pixels(gray_values) -> bytes:
    import io

    from PIL import Image

    img = Image.new("L", (4, 4))
    img.putdata(list(gray_values))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid_png() -> bytes:
    """纯黑帧——headless dummy 渲染器空帧的形态。"""
    return _png_pixels([0] * 16)


def _noise_png() -> bytes:
    import random

    return _png_pixels([random.randint(0, 255) for _ in range(16)])


def _patch_engine(monkeypatch, behavior_by_mode):
    """把 subprocess.run 换成按渲染模式（--headless 有无）分派的假引擎。"""
    from pathlib import Path
    from unittest.mock import MagicMock

    def fake_run(cmd, **kwargs):
        out_dir = Path(kwargs["env"]["GAMEFORGE_PLAYTEST_OUT"])
        frames_dir = out_dir / "frames"
        mode = "headless" if "--headless" in cmd else "windowed"
        behavior_by_mode[mode](out_dir, frames_dir)
        return MagicMock(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("src.engine.godot.playtest.subprocess.run", fake_run)


def _write_report(out_dir, actions_executed, frames_captured):
    import json

    (out_dir / "report.json").write_text(json.dumps({
        "schema": REPORT_SCHEMA,
        "ok": True, "actions_executed": actions_executed, "frames_captured": frames_captured,
    }), encoding="utf-8")


class TestBlankFrames:
    def test_solid_frames_detected_blank(self, tmp_path):
        from src.engine.godot.playtest import detect_blank_frames

        d = tmp_path / "f"
        d.mkdir(parents=True, exist_ok=True)  # basetemp 残留目录容忍
        for i in range(3):
            (d / f"f{i}.png").write_bytes(_solid_png())
        assert detect_blank_frames(d, ["f0.png", "f1.png", "f2.png"])["blank"] is True

    def test_noise_frames_not_blank(self, tmp_path):
        from src.engine.godot.playtest import detect_blank_frames

        d = tmp_path / "f"
        d.mkdir(parents=True, exist_ok=True)  # basetemp 残留目录容忍
        for i in range(3):
            (d / f"f{i}.png").write_bytes(_noise_png())
        assert detect_blank_frames(d, ["f0.png", "f1.png", "f2.png"])["blank"] is False

    def test_missing_or_unjudgeable(self, tmp_path):
        from src.engine.godot.playtest import detect_blank_frames

        assert detect_blank_frames(tmp_path, [])["blank"] is None


class TestRenderingFallback:
    def _setup(self, tmp_path, monkeypatch, **pt_cfg):
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        proj = tmp_path / "proj"
        (proj / "addons" / "gameforge").mkdir(parents=True, exist_ok=True)
        (proj / "project.godot").write_text("[autoload]\n", encoding="utf-8")
        (tmp_path / "fake_godot.exe").write_text("", encoding="utf-8")
        r = PlaytestRunner({
            "godot": {"editor_path": str(tmp_path / "fake_godot.exe"), "project_path": str(proj)},
            "playtest": {"skip_when_unavailable": True, **pt_cfg},
        })
        return r

    def test_blank_headless_falls_back_to_windowed(self, tmp_path, monkeypatch):
        behaviors = {
            "headless": lambda out, f: (
                f.mkdir(parents=True),
                (f / "f00000.png").write_bytes(_solid_png()),
                (f / "f00001.png").write_bytes(_solid_png()),
                _write_report(out, 2, 2),
            ),
            "windowed": lambda out, f: (
                f.mkdir(parents=True),
                (f / "f00000.png").write_bytes(_noise_png()),
                (f / "f00001.png").write_bytes(_noise_png()),
                (f / "f00002.png").write_bytes(_noise_png()),
                _write_report(out, 3, 3),
            ),
        }
        _patch_engine(monkeypatch, behaviors)
        r = self._setup(tmp_path, monkeypatch)  # rendering_mode 默认 auto
        result = r.run([{"t": 0.5, "type": "key", "key": "Right", "pressed": True}],
                       project_id="p1", run_id="r1")
        assert result["rendering_mode"] == "windowed"
        assert result["ok"] is True
        assert result["report"]["frames_captured"] == 3
        assert any("空帧" in w for w in result["warnings"])

    def test_forced_headless_never_falls_back(self, tmp_path, monkeypatch):
        behaviors = {
            "headless": lambda out, f: (
                f.mkdir(parents=True),
                (f / "f00000.png").write_bytes(_solid_png()),
                _write_report(out, 1, 1),
            ),
            "windowed": lambda out, f: (_ for _ in ()).throw(AssertionError("不应回退窗口模式")),
        }
        _patch_engine(monkeypatch, behaviors)
        r = self._setup(tmp_path, monkeypatch, rendering_mode="headless")
        result = r.run([{"t": 0.5, "type": "key", "key": "Right", "pressed": True}],
                       project_id="p1", run_id="r1")
        assert result["rendering_mode"] == "headless"
        assert result["ok"] is True

    def test_frameless_headless_falls_back_to_windowed(self, tmp_path, monkeypatch):
        """headless 连帧都抓不到（dummy 渲染器无图像）→ 也应回退窗口模式。"""
        behaviors = {
            "headless": lambda out, f: _write_report(out, 2, 0),  # 无 frames 目录
            "windowed": lambda out, f: (
                f.mkdir(parents=True),
                (f / "f00000.png").write_bytes(_noise_png()),
                _write_report(out, 3, 1),
            ),
        }
        _patch_engine(monkeypatch, behaviors)
        r = self._setup(tmp_path, monkeypatch)  # auto
        result = r.run([{"t": 0.5, "type": "key", "key": "Right", "pressed": True}],
                       project_id="p1", run_id="r1")
        assert result["rendering_mode"] == "windowed"
        assert result["report"]["frames_captured"] == 1

    def test_windowed_mode_runs_directly(self, tmp_path, monkeypatch):
        behaviors = {
            "headless": lambda out, f: (_ for _ in ()).throw(AssertionError("不应启动 headless")),
            "windowed": lambda out, f: (
                f.mkdir(parents=True),
                (f / "f00000.png").write_bytes(_noise_png()),
                _write_report(out, 1, 1),
            ),
        }
        _patch_engine(monkeypatch, behaviors)
        r = self._setup(tmp_path, monkeypatch, rendering_mode="windowed")
        result = r.run([{"t": 0.5, "type": "key", "key": "Right", "pressed": True}],
                       project_id="p1", run_id="r1")
        assert result["rendering_mode"] == "windowed"
        assert result["ok"] is True
