"""VLM 视觉审查测试。

不调真实视觉模型：审帧采样、提示词、降级逻辑、结果落盘全部离线验证，
模型响应通过 mock LLMClient 注入。
"""
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.paths as paths
from src.engine.godot.visual_review import (
    VISUAL_REVIEW_SCHEMA,
    VisualReviewer,
    pick_frames,
)


def _make_config(enabled=True, provider="zhipu", model="glm-4v-plus"):
    return {
        "visual_review": {"enabled": enabled, "max_frames": 4},
        "llm": {
            "providers": {"zhipu": {"base_url": "https://example.com/v1", "api_key_env": "_TEST_VLM_KEY"}},
            "models": {"visual_reviewer": {"provider": provider, "model": model}},
        },
    }


@pytest.fixture()
def frames_dir(tmp_path):
    d = tmp_path / "frames"
    d.mkdir(parents=True, exist_ok=True)  # basetemp 确定性复用，残留目录需容忍
    # 1x1 PNG
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
        "h6FO1AAAAABJRU5ErkJggg=="
    )
    for i in range(10):
        (d / f"f{i:05d}.png").write_bytes(png)
    return d


class TestPickFrames:
    def test_few_frames_all_picked(self):
        names = [f"f{i}.png" for i in range(3)]
        assert pick_frames(names, 4) == names

    def test_even_sampling_with_ends(self):
        names = [f"f{i:05d}.png" for i in range(100)]
        picked = pick_frames(names, 4)
        assert len(picked) == 4
        assert picked[0] == "f00000.png"
        assert picked[-1] == "f00099.png"

    def test_empty(self):
        assert pick_frames([], 4) == []


class TestAvailable:
    def test_disabled_reported_by_enabled_flag(self):
        r = VisualReviewer(_make_config(enabled=False))
        assert r.enabled is False

    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("_TEST_VLM_KEY", raising=False)
        r = VisualReviewer(_make_config())
        ok, why = r.available()
        assert ok is False

    def test_available_with_key(self, monkeypatch):
        monkeypatch.setenv("_TEST_VLM_KEY", "sk-test")
        r = VisualReviewer(_make_config())
        ok, _ = r.available()
        assert ok is True


class TestReview:
    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, frames_dir):
        r = VisualReviewer(_make_config(enabled=False))
        out = await r.review(frames_dir, ["f00000.png"], project_id="p1", run_id="r1")
        assert out["skipped"] is True
        assert out["reason"] == "visual_review.disabled"

    @pytest.mark.asyncio
    async def test_skips_without_key(self, frames_dir, monkeypatch):
        monkeypatch.delenv("_TEST_VLM_KEY", raising=False)
        r = VisualReviewer(_make_config(enabled=True))
        out = await r.review(frames_dir, ["f00000.png"], project_id="p1", run_id="r1")
        assert out["skipped"] is True

    @pytest.mark.asyncio
    async def test_pass_review_writes_result(self, frames_dir, monkeypatch, tmp_path):
        monkeypatch.setenv("_TEST_VLM_KEY", "sk-test")
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        r = VisualReviewer(_make_config())

        mock_llm = MagicMock()
        mock_llm.chat_with_images = AsyncMock(return_value=(
            '{"overall_pass": true, "summary": "画面正常", "issues": []}'
        ))
        with patch("src.utils.llm_client.get_llm_client", return_value=mock_llm):
            out = await r.review(frames_dir, [f"f{i:05d}.png" for i in range(10)],
                                 project_id="p1", run_id="r1")

        assert out["skipped"] is False
        assert out["ok"] is True
        assert len(out["reviewed_frames"]) == 4  # max_frames 采样
        # 证据落盘
        saved = paths.read_json(paths.visual_review_path("p1", "r1"))
        assert saved and saved["ok"] is True
        # 发给模型的是多模态消息（最后一层 content 是 parts 列表）
        sent = mock_llm.chat_with_images.await_args
        assert sent.args[0][0]["role"] == "system"
        assert sent.args[0][0]["content"].startswith("你是游戏画面质量审查员")
        imgs = sent.args[1]
        assert len(imgs) == 4 and imgs[0]["mime"] == "image/png"

    @pytest.mark.asyncio
    async def test_high_severity_fails_review(self, frames_dir, monkeypatch, tmp_path):
        monkeypatch.setenv("_TEST_VLM_KEY", "sk-test")
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        r = VisualReviewer(_make_config())

        mock_llm = MagicMock()
        mock_llm.chat_with_images = AsyncMock(return_value=(
            '{"overall_pass": true, "summary": "有黑屏", '
            '"issues": [{"severity": "high", "area": "render", '
            '"description": "第3帧黑屏", "frame_index": 2}]}'
        ))
        with patch("src.utils.llm_client.get_llm_client", return_value=mock_llm):
            out = await r.review(frames_dir, ["f00000.png"], project_id="p1", run_id="r1")

        # overall_pass=true 但有 high 问题 → 仍判未通过
        assert out["ok"] is False
        assert out["issues"][0]["severity"] == "high"

    @pytest.mark.asyncio
    async def test_llm_failure_degrades_to_skipped(self, frames_dir, monkeypatch, tmp_path):
        monkeypatch.setenv("_TEST_VLM_KEY", "sk-test")
        monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
        r = VisualReviewer(_make_config())

        mock_llm = MagicMock()
        mock_llm.chat_with_images = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("src.utils.llm_client.get_llm_client", return_value=mock_llm):
            out = await r.review(frames_dir, ["f00000.png"], project_id="p1", run_id="r1")

        assert out["skipped"] is True
        assert "boom" in out.get("reason", "")


class TestSchema:
    def test_schema_marker(self):
        assert VISUAL_REVIEW_SCHEMA == "gameforge.visual_review.v1"
