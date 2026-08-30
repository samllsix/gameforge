"""AI 素材锻造与场景接线测试。"""
import os

import pytest

from src.engine.godot.scene_to_godot import build_scene_tscn, default_scene_ir


# ── build_scene_tscn 素材接线 ────────────────────────────────────────────────

_ASSETS = {
    "background": "res://assets/gen/background.png",
    "player": "res://assets/gen/player.png",
    "enemy": "res://assets/gen/enemy.png",
    "pickup": "res://assets/gen/pickup.png",
}


def test_tscn_without_assets_has_no_sprites():
    tscn = build_scene_tscn(default_scene_ir(), width=320, height=180)
    assert "Sprite2D" not in tscn
    assert 'type="Texture2D"' not in tscn


def test_tscn_with_assets_emits_sprites():
    tscn = build_scene_tscn(default_scene_ir(), width=320, height=180, assets=_ASSETS)
    # 纹理 ext_resource
    assert tscn.count('type="Texture2D"') == 4
    # 玩家/敌人/金币精灵挂在视觉节点下，3 层背景各一张 BGArt
    assert tscn.count("Sprite2D") >= 3 + 3
    assert '[node name="Sprite" type="Sprite2D" parent="PlayerVisual"]' in tscn
    assert '[node name="Sprite" type="Sprite2D" parent="Enemy1/Visual"]' in tscn
    assert '[node name="Sprite" type="Sprite2D" parent="Pickup1/Visual"]' in tscn
    assert '[node name="BGArt1" type="Sprite2D" parent="ParallaxLayer1"]' in tscn
    # 像素风最近邻采样
    assert "texture_filter = 1" in tscn
    # 脚本引用仍完整（动画载体未被破坏）
    assert 'type="Script"' in tscn


def test_tscn_partial_assets_only_wires_present_ones():
    tscn = build_scene_tscn(
        default_scene_ir(), width=320, height=180,
        assets={"player": "res://assets/gen/player.png"},
    )
    assert tscn.count('type="Texture2D"') == 1
    assert "BGArt1" not in tscn
    assert '[node name="Sprite" type="Sprite2D" parent="PlayerVisual"]' in tscn


# ── asset_forge 门控与生成 ───────────────────────────────────────────────────

def test_forge_disabled_by_env(monkeypatch, tmp_path):
    from src.engine.godot import asset_forge
    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "0")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    assert asset_forge.forge_assets(default_scene_ir(), str(tmp_path)) == {}


def test_forge_skips_without_provider(monkeypatch, tmp_path):
    from src.engine.godot import asset_forge
    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: False)
    assert asset_forge.forge_assets(default_scene_ir(), str(tmp_path)) == {}


def test_forge_generates_and_caches(monkeypatch, tmp_path):
    import shutil

    from src.engine.godot import asset_forge
    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    shutil.rmtree(tmp_path / "assets", ignore_errors=True)  # tmp_path 跨运行复用，先清理

    calls = {"n": 0}

    def fake_generate_one(key, project_path, timeout, prompt=None):
        out = os.path.join(project_path, "assets", "gen", f"{key}.png")
        if os.path.isfile(out):  # 与真实 _generate_one 一致：先查文件缓存
            return "res://assets/gen/" + key + ".png"
        calls["n"] += 1
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from PIL import Image

        Image.new("RGBA", (512, 512), (10, 10, 10, 255)).save(out)
        return "res://assets/gen/" + key + ".png"

    monkeypatch.setattr(asset_forge, "_generate_one", fake_generate_one)
    assets = asset_forge.forge_assets(default_scene_ir(), str(tmp_path))
    assert set(assets) == {"background", "player", "enemy", "pickup", "icon", "ground", "platform", "decoration", "npc"}
    assert calls["n"] == 9

    # 第二次调用命中文件缓存，不再触发生成
    assets2 = asset_forge.forge_assets(default_scene_ir(), str(tmp_path))
    assert assets2 == assets
    assert calls["n"] == 9


def test_remove_background_makes_corners_transparent():
    from PIL import Image

    from src.engine.godot.asset_forge import _remove_background

    img = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    px = img.load()
    for x in range(24, 40):
        for y in range(24, 40):
            px[x, y] = (200, 30, 30, 255)  # 中心红色方块

    out = _remove_background(img)
    assert out.load()[2, 2][3] == 0        # 角落被抠透明
    assert out.load()[32, 32][3] == 255    # 中心主体保留


def test_generate_one_normalizes_size(monkeypatch, tmp_path):
    """API 实际返回尺寸与请求不同（如 Step 512→1024）时必须归一化到请求尺寸"""
    from src.engine.godot import asset_forge
    from PIL import Image

    big = tmp_path / "raw.png"
    Image.new("RGBA", (1024, 1024), (5, 5, 5, 255)).save(big)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def generate_image(self, prompt, size=None):
            return {"success": True, "image_path": str(big)}

    monkeypatch.setattr("src.image.ai_image_client.AIImageClient", FakeClient)
    project = tmp_path / "proj"
    path = asset_forge._generate_one("player", str(project), timeout=10)
    assert path == "res://assets/gen/player.png"
    out = Image.open(project / "assets" / "gen" / "player.png")
    assert out.size == (512, 512)
