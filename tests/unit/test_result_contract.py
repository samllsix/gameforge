"""操作结果统一契约测试（src/core/result）。

两层验证：
1. 契约函数自身的规则（构造、冲突、校验）；
2. 三个验证模块（runtime_smoke / playtest / visual_review）的返回值
   在离线路径下必须合规——"统一结果契约"靠这里防回归。
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.paths as paths
from src.core.result import (
    OPERATION_RESULT_SCHEMA,
    add_artifact,
    is_contract_valid,
    new_operation_result,
    validate_operation_result,
)


# ── 契约函数自身 ─────────────────────────────────────────────────────────────


class TestResultBuilder:
    def test_base_shape(self):
        r = new_operation_result("x.y", ok=True)
        assert r["schema"] == OPERATION_RESULT_SCHEMA
        assert r["operation"] == "x.y"
        assert r["ok"] is True and r["skipped"] is False
        assert r["artifacts"] == {} and r["warnings"] == [] and r["errors"] == []

    def test_extra_merge_and_conflict(self):
        r = new_operation_result("x.y", extra={"custom": 1})
        assert r["custom"] == 1
        with pytest.raises(ValueError, match="extra 键与信封键冲突"):
            new_operation_result("x.y", extra={"ok": 1})

    def test_add_artifact_returns_path_str(self, tmp_path):
        r = new_operation_result("x.y")
        ret = add_artifact(r, "out", tmp_path / "a.json")
        assert ret == str(tmp_path / "a.json")
        assert r["artifacts"]["out"] == ret


class TestValidateResult:
    def test_valid_minimal(self):
        assert validate_operation_result(new_operation_result("x.y", ok=True)) == []

    def test_not_a_dict(self):
        assert validate_operation_result(None)
        assert validate_operation_result([1])

    def test_wrong_schema(self):
        r = new_operation_result("x.y", ok=True)
        r["schema"] = "other.v1"
        assert any("schema" in i for i in validate_operation_result(r))

    def test_skip_reason_rules(self):
        r = new_operation_result("x.y", skipped=True)  # 缺 skip_reason
        assert any("skip_reason" in i for i in validate_operation_result(r))
        r2 = new_operation_result("x.y", ok=True, skip_reason="why")
        assert any("skip_reason" in i for i in validate_operation_result(r2))

    def test_failure_must_be_attributable(self):
        r = new_operation_result("x.y", ok=False)
        assert any("errors 必须非空" in i for i in validate_operation_result(r))
        r["errors"].append("boom")
        assert validate_operation_result(r) == []

    def test_run_scoped_ops_carry_addressing(self):
        """run 级操作（产物按 (project_id, run_id) 编址）的编址约定。"""
        r = new_operation_result("playtest.run", ok=True, project_id="p1", run_id="r1")
        assert validate_operation_result(r) == []
        assert r["project_id"] == "p1" and r["run_id"] == "r1"


# ── 三个验证模块的返回值合规 ─────────────────────────────────────────────────


class TestRuntimeSmokeContract:
    def test_skip_path_to_dict_conforms(self, tmp_path, monkeypatch):
        # 必须对宿主环境免疫：.env/导出的 GODOT_* 会绕过 skip 分支
        monkeypatch.delenv("GODOT_EDITOR_PATH", raising=False)
        monkeypatch.delenv("GODOT_PROJECT_PATH", raising=False)
        from src.engine.godot.runtime_smoke import GodotRuntimeSmoke

        smoke = GodotRuntimeSmoke({"godot": {"editor_path": "", "project_path": str(tmp_path)}})
        result = smoke.run_scene("res://scenes/Main.tscn")
        d = result.to_dict()
        assert is_contract_valid(d)
        assert d["skipped"] is True and d["skip_reason"]
        # 旧键保留（评测读 runnable；workflow 读 errors）
        assert d["runnable"] is True
        assert d["errors"][0]["pattern"] == "SKIPPED"


class TestPlaytestContract:
    def _runner(self, tmp_path, monkeypatch, **pt_cfg):
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        from src.engine.godot.playtest import PlaytestRunner

        return PlaytestRunner({
            "godot": {
                "editor_path": str(tmp_path / "fake_godot.exe"),
                "project_path": str(tmp_path / "proj"),
            },
            "playtest": {"skip_when_unavailable": True, **pt_cfg},
        })

    def _make_project(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "addons" / "gameforge").mkdir(parents=True, exist_ok=True)
        (proj / "project.godot").write_text("[autoload]\n", encoding="utf-8")
        (tmp_path / "fake_godot.exe").write_text("", encoding="utf-8")
        return proj

    def test_skip_path_conforms(self, tmp_path, monkeypatch):
        result = self._runner(tmp_path, monkeypatch).run(
            [{"t": 0.5, "type": "key", "key": "Right", "pressed": True}], project_id="p1",
        )
        assert is_contract_valid(result)
        assert result["skipped"] is True and result["skip_reason"]

    def test_success_run_conforms_with_artifacts(self, tmp_path, monkeypatch):
        self._make_project(tmp_path)

        def behavior(out_dir, frames_dir):
            frames_dir.mkdir(parents=True)
            for i in range(2):
                (frames_dir / f"f{i:05d}.png").write_bytes(_noise_png())
            (out_dir / "report.json").write_text(json.dumps({
                "schema": "gameforge.playtest_report.v1",
                "ok": True, "actions_executed": 3, "frames_captured": 2,
            }), encoding="utf-8")

        _patch_engine(monkeypatch, behavior)
        result = self._runner(tmp_path, monkeypatch, rendering_mode="headless").run(
            [{"t": 0.5, "type": "key", "key": "Right", "pressed": True}],
            project_id="p1", run_id="r1",
        )
        assert is_contract_valid(result)
        assert result["ok"] is True
        assert result["rendering_mode"] == "headless"
        assert set(result["artifacts"]) == {"actions", "report", "frames_dir"}
        # findings 与 errors 是同一列表对象（历史别名）
        assert result["findings"] is result["errors"]


# ── 视觉审查合规（复用 test_visual_review 的离线 mock 手法）─────────────────


def _vrm_config(enabled=True):
    return {
        "visual_review": {"enabled": enabled, "max_frames": 4},
        "llm": {
            "providers": {"zhipu": {"base_url": "https://example.com/v1", "api_key_env": "_TEST_VLM_KEY"}},
            "models": {"visual_reviewer": {"provider": "zhipu", "model": "glm-4v-plus"}},
        },
    }


_NOISE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)


def _noise_png() -> bytes:
    """4x4 随机噪声 PNG——灰度 stddev 远超空帧阈值。"""
    import io
    import random

    from PIL import Image

    img = Image.new("L", (4, 4))
    img.putdata([random.randint(0, 255) for _ in range(16)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _patch_engine(monkeypatch, behavior):
    """把 playtest 的 subprocess.run 换成按渲染模式分派的假引擎。"""
    from unittest.mock import MagicMock as _M

    def fake_run(cmd, **kwargs):
        out_dir = paths.Path(kwargs["env"]["GAMEFORGE_PLAYTEST_OUT"])
        frames_dir = out_dir / "frames"
        if "--headless" in cmd:
            behavior(out_dir, frames_dir)
        return _M(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("src.engine.godot.playtest.subprocess.run", fake_run)


class TestVisualReviewContract:
    @pytest.fixture()
    def frames_dir(self, tmp_path):
        d = tmp_path / "frames"
        d.mkdir(parents=True, exist_ok=True)
        png = base64.b64decode(_NOISE_PNG_B64)
        for i in range(4):
            (d / f"f{i:05d}.png").write_bytes(png)
        return d

    @pytest.mark.asyncio
    async def test_skip_path_conforms(self, frames_dir):
        from src.engine.godot.visual_review import VisualReviewer

        r = VisualReviewer(_vrm_config(enabled=False))
        out = await r.review(frames_dir, ["f00000.png"], project_id="p1", run_id="r1")
        assert is_contract_valid(out)
        assert out["skipped"] is True
        assert out["reason"] == out["skip_reason"] == "visual_review.disabled"

    @pytest.mark.asyncio
    async def test_fail_path_errors_attributable(self, frames_dir, monkeypatch, tmp_path):
        monkeypatch.setenv("_TEST_VLM_KEY", "sk-test")
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        from src.engine.godot.visual_review import VisualReviewer

        r = VisualReviewer(_vrm_config())
        mock_llm = MagicMock()
        mock_llm.chat_with_images = AsyncMock(return_value=(
            '{"overall_pass": false, "summary": "黑屏", '
            '"issues": [{"severity": "high", "area": "render", "description": "全黑", "frame_index": 0}]}'
        ))
        with patch("src.utils.llm_client.get_llm_client", return_value=mock_llm):
            out = await r.review(frames_dir, ["f00000.png"], project_id="p1", run_id="r1")
        assert is_contract_valid(out)
        assert out["ok"] is False
        assert out["errors"], "失败必须可归因"
        assert any("high" in e for e in out["errors"])
