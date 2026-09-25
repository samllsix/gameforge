"""美术指导器测试：主题驱动规划 / 回退 / 接线。"""
import os

import pytest

from src.agents.art_director import _fallback_plan, plan_art
from src.engine.godot.scene_to_godot import default_scene_ir


def _no_llm(monkeypatch):
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")


def test_fallback_uses_theme_nouns(monkeypatch):
    _no_llm(monkeypatch)
    ir = default_scene_ir(theme="farm", genre="farming_sim")
    plan = plan_art(ir)
    # 农场主题：玩家是农夫、敌人是乌鸦、地面是草地
    assert "farmer" in plan["player"]
    assert "crow" in plan["enemy"]
    assert "grass" in plan["ground"]
    # 9 个槽位全齐
    assert len(plan) == 9


def test_fallback_unknown_theme_generic(monkeypatch):
    _no_llm(monkeypatch)
    ir = default_scene_ir(theme="sky_blue", genre="platformer")
    plan = plan_art(ir)
    assert len(plan) == 9
    assert "hero character" in plan["player"]


def test_llm_plan_used_when_valid(monkeypatch):
    _no_llm(monkeypatch)
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "1")
    monkeypatch.setattr("src.utils.llm_client.get_llm_client", lambda config: _FakeLLM())
    ir = default_scene_ir(theme="neon_city", genre="shooter")

    plan = plan_art(ir)
    assert plan["player"] == "neon player sprite"  # _FakeLLM 产出格式


def test_llm_partial_plan_falls_back_per_slot(monkeypatch):
    _no_llm(monkeypatch)
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "1")

    class _Partial:
        def chat_sync(self, messages, **kw):
            return '{"background": "cyber skyline"}'  # 只有 1 槽

    monkeypatch.setattr("src.utils.llm_client.get_llm_client", lambda config: _Partial())
    ir = default_scene_ir(theme="neon_city", genre="shooter")
    plan = plan_art(ir)
    assert plan["background"] == "cyber skyline"       # LLM 的被采用
    assert len(plan) == 9                              # 缺槽回落补齐


class _FakeLLM:
    def chat_sync(self, messages, **kw):
        import json

        return json.dumps({slot: f"neon {slot} sprite" for slot in [
            "background", "player", "enemy", "pickup", "ground",
            "platform", "decoration", "npc", "icon"]})


class _CapturingLLM:
    """捕获 prompt 与采样参数，用于 P2 断言。"""

    def __init__(self):
        self.calls = []

    def chat_sync(self, messages, **kw):
        self.calls.append({"content": messages[0]["content"], "kw": kw})
        import json

        return json.dumps({"background": "cyber skyline"})


def test_llm_prompt_allows_style_words_with_shared_design_language(monkeypatch):
    """P2：art_director prompt 放开风格词（光照/描边/剪影），降温到 0.4。"""
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "1")
    fake = _CapturingLLM()
    monkeypatch.setattr("src.utils.llm_client.get_llm_client", lambda config: fake)
    ir = default_scene_ir(theme="neon_city", genre="shooter")

    plan_art(ir)
    assert fake.calls, "LLM 应被调用一次"
    content = fake.calls[0]["content"]
    # 不再禁止风格词；改为要求统一画法
    assert "no style keywords" not in content
    assert "lighting" in content
    assert "outline" in content
    assert "silhouette" in content
    # 0.8 偏高 → 0.4（同主题两次运行视觉差异大）
    assert fake.calls[0]["kw"].get("temperature") == 0.4


def test_forge_assets_accepts_art_prompts(monkeypatch, tmp_path):
    from src.engine.godot import asset_forge
    from src.engine.godot.scene_to_godot import default_scene_ir

    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    import shutil

    shutil.rmtree(tmp_path / "assets", ignore_errors=True)

    captured = {}

    def fake_generate_one(key, project_path, timeout, prompt=None, pixel_ctx=None, style_ctx=None,
                          reference_paths=None, out_key="", same_subject=False):
        captured[out_key or key] = prompt
        out = os.path.join(project_path, "assets", "gen", f"{out_key or key}.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from PIL import Image

        Image.new("RGBA", (128, 128), (10, 10, 10, 255)).save(out)
        return "res://assets/gen/" + (out_key or key) + ".png"

    monkeypatch.setattr(asset_forge, "_generate_one", fake_generate_one)
    art = {"player": "cyber courier with jetpack", "enemy": "rogue patrol drone"}
    assets = asset_forge.forge_assets(
        default_scene_ir(), str(tmp_path), art_prompts=art
    )
    assert captured["player"] == "cyber courier with jetpack"
    assert captured["enemy"] == "rogue patrol drone"
    # 静态素材 9 件 + 玩家序列帧产物（player_anim / player_frames）
    assert set(assets) >= {
        "background", "player", "enemy", "pickup", "icon",
        "ground", "platform", "decoration", "npc",
    }
    assert "player_anim" in assets
