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
    assert '[node name="Sprite" type="Sprite2D" parent="Player/PlayerVisual"]' in tscn
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
    assert '[node name="Sprite" type="Sprite2D" parent="Player/PlayerVisual"]' in tscn


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


def _fresh_project(tmp_path):
    """确定性 tmp 目录跨运行复用：开测前清空项目目录，保证测试可重跑。"""
    import shutil

    shutil.rmtree(tmp_path, ignore_errors=True)
    os.makedirs(tmp_path, exist_ok=True)


def test_forge_generates_and_caches(monkeypatch, tmp_path):
    import shutil

    from src.engine.godot import asset_forge
    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    # plan_art 已接入生产路径；关掉 LLM 让其走母题兜底，保证测试离线确定
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    shutil.rmtree(tmp_path / "assets", ignore_errors=True)  # tmp_path 跨运行复用，先清理

    calls = {"n": 0}

    def fake_generate_one(key, project_path, timeout, prompt=None, pixel_ctx=None, style_ctx=None,
                          reference_paths=None, out_key="", same_subject=False):
        out = os.path.join(project_path, "assets", "gen", f"{out_key or key}.png")
        if os.path.isfile(out):  # 与真实 _generate_one 一致：先查文件缓存
            return "res://assets/gen/" + (out_key or key) + ".png"
        calls["n"] += 1
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from PIL import Image

        Image.new("RGBA", (512, 512), (10, 10, 10, 255)).save(out)
        return "res://assets/gen/" + (out_key or key) + ".png"

    monkeypatch.setattr(asset_forge, "_generate_one", fake_generate_one)
    _fresh_project(tmp_path)
    assets = asset_forge.forge_assets(default_scene_ir(), str(tmp_path))
    # 静态素材 9 件 + 敌人变体 2 件（P4 i2i）+ 序列帧元数据 2 项
    assert set(assets) == {
        "background", "player", "enemy", "enemy2", "enemy3", "pickup", "icon",
        "ground", "platform", "decoration", "npc",
        "player_anim", "player_frames",
    }
    # 第一波 8 槽位（npc 移入第二波）+ 第二波 npc（player 锚）+ 2 个敌人变体（enemy 参考图）
    assert calls["n"] == 11

    # 第二次调用命中文件缓存，不再触发生成
    assets2 = asset_forge.forge_assets(default_scene_ir(), str(tmp_path))
    assert assets2 == assets
    assert calls["n"] == 11


def test_forge_wires_plan_art_and_style_ctx(monkeypatch, tmp_path):
    """P1/P2 接线：art_prompts 缺省时走 plan_art 美术指导书，
    并把 {genre, palette_base, camera} 风格上下文透传给 _generate_one。"""
    import src.engine.godot.character_anim as character_anim
    from src.engine.godot import asset_forge

    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    # 本测试只验证提示词与风格上下文接线，跳过真实序列帧构建（省时且不添删除流量）
    monkeypatch.setattr(character_anim, "build_player_animations", lambda *a, **k: {"ok": False})

    seen = {}

    def fake_generate_one(key, project_path, timeout, prompt=None, pixel_ctx=None, style_ctx=None,
                          reference_paths=None, out_key="", same_subject=False):
        seen[out_key or key] = (prompt, style_ctx, reference_paths, same_subject)
        return "res://assets/gen/" + (out_key or key) + ".png"

    monkeypatch.setattr(asset_forge, "_generate_one", fake_generate_one)
    # farm 主题（palette_base=forest_green）+ 默认相机 2d_side_view
    ir = default_scene_ir(theme="farm", genre="platformer")
    asset_forge.forge_assets(ir, str(tmp_path))

    # plan_art 兜底模板接驳：农场主题玩家是农夫
    prompt, style_ctx, _, _ = seen["player"]
    assert "farmer" in prompt
    assert style_ctx["genre"] == "platformer"
    assert style_ctx["palette_base"] == "forest_green"
    assert style_ctx["camera"] == "2d_side_view"


def test_forge_two_wave_i2i_anchor_and_variants(monkeypatch, tmp_path):
    """P3/P4：npc 以 player.png 为参考图走第二波（anchor 模式）；
    敌人变体以 enemy.png 为参考图、edit 模式；player 必须先于 npc 落盘。"""
    import src.engine.godot.character_anim as character_anim
    from src.engine.godot import asset_forge

    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    monkeypatch.setattr(character_anim, "build_player_animations", lambda *a, **k: {"ok": False})
    _fresh_project(tmp_path)

    order = []
    jobs = {}

    def fake_generate_one(key, project_path, timeout, prompt=None, pixel_ctx=None, style_ctx=None,
                          reference_paths=None, out_key="", same_subject=False):
        name = out_key or key
        order.append(name)
        jobs[name] = {
            "prompt": prompt,
            "reference_paths": reference_paths,
            "same_subject": same_subject,
            "style_ctx": style_ctx,
        }
        out = os.path.join(project_path, "assets", "gen", f"{name}.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from PIL import Image

        Image.new("RGBA", (512, 512), (10, 10, 10, 255)).save(out)
        return "res://assets/gen/" + name + ".png"

    monkeypatch.setattr(asset_forge, "_generate_one", fake_generate_one)
    asset_forge.forge_assets(default_scene_ir(theme="farm", genre="platformer"), str(tmp_path))

    # 两波顺序：player（第一波）先于 npc（第二波）
    assert order.index("player") < order.index("npc")
    # npc 带 player 风格锚，anchor 模式（不同主体）
    npc_refs = jobs["npc"]["reference_paths"] or []
    assert any(p.endswith("player.png") for p in npc_refs)
    assert jobs["npc"]["same_subject"] is False
    # 敌人变体带 enemy 参考图，edit 模式（保留剪影换色）
    for vkey in ("enemy2", "enemy3"):
        assert jobs[vkey]["same_subject"] is True
        assert any(p.endswith("enemy.png") for p in (jobs[vkey]["reference_paths"] or []))
        assert "color scheme" in jobs[vkey]["prompt"]
    # 第二波仍带风格上下文（farm → forest_green）
    assert jobs["npc"]["style_ctx"]["palette_base"] == "forest_green"


def test_forge_i2i_disabled_uses_legacy_path(monkeypatch, tmp_path):
    """开关全关 → 旧行为：npc 并入第一波（无参考图），变体走 _hue_variants 色相偏移。"""
    import src.engine.godot.character_anim as character_anim
    from src.engine.godot import asset_forge

    monkeypatch.setenv("GAMEFORGE_ASSETS_ENABLED", "1")
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    monkeypatch.setenv("GAMEFORGE_I2I_ANCHOR", "0")
    monkeypatch.setenv("GAMEFORGE_I2I_VARIANTS", "0")
    monkeypatch.setattr(asset_forge, "_providers_available", lambda: True)
    monkeypatch.setattr(character_anim, "build_player_animations", lambda *a, **k: {"ok": False})
    _fresh_project(tmp_path)

    hue_calls = []
    monkeypatch.setattr(asset_forge, "_hue_variants", lambda src, count, palette=None: hue_calls.append((src, count)))

    def fake_generate_one(key, project_path, timeout, prompt=None, pixel_ctx=None, style_ctx=None,
                          reference_paths=None, out_key="", same_subject=False):
        name = out_key or key
        assert not reference_paths, "开关关闭时不应传参考图"
        out = os.path.join(project_path, "assets", "gen", f"{name}.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from PIL import Image

        Image.new("RGBA", (512, 512), (10, 10, 10, 255)).save(out)
        return "res://assets/gen/" + name + ".png"

    monkeypatch.setattr(asset_forge, "_generate_one", fake_generate_one)
    assets = asset_forge.forge_assets(default_scene_ir(), str(tmp_path))

    # npc 走了第一波（无参考图），产物里没有 i2i 变体
    assert "npc" in assets
    assert "enemy2" not in assets and "enemy3" not in assets
    # 色相偏移保底被触发（enemy.png 已由 fake 落盘）
    assert hue_calls and hue_calls[0][1] == 2


def test_i2i_prompt_wraps_content():
    """P3：anchor 模式包"风格锚 + 不同主体"；edit 模式包"保留剪影 + 换色描述"。"""
    from src.engine.godot.asset_forge import _i2i_prompt

    anchor = _i2i_prompt("villager NPC sprite")
    assert "art-style anchor" in anchor
    assert "different subject" in anchor
    assert "villager NPC sprite" in anchor

    edit = _i2i_prompt("crimson red color scheme", same_subject=True)
    assert "silhouette" in edit
    assert "crimson red color scheme" in edit


def _circle_mask_img(size, radius_frac, color):
    """画一个居中圆（alpha 圆外透明）的 RGBA 图。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * radius_frac)
    d.ellipse([size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r], fill=color)
    return img


def test_variant_alpha_guard_clears_gray_box():
    """P4：变体带不透明灰底（抠底保守跳过）→ 参考图掩膜清底，覆盖度回落。"""
    import numpy as np
    from PIL import Image

    from src.engine.godot.asset_forge import _variant_alpha_guard

    ref = _circle_mask_img(512, 0.15, (150, 80, 190, 255))
    ref_path = ".tmp/guard_ref.png"
    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    ref.save(ref_path)

    # 灰底 box：整图不透明，只有中心一小块是紫的（模拟 remove_background 跳过）
    box = Image.new("RGBA", (512, 512), (128, 128, 128, 255))
    box.paste(_circle_mask_img(512, 0.1, (150, 80, 190, 255)), (0, 0), _circle_mask_img(512, 0.1, (150, 80, 190, 255)))

    out, iou = _variant_alpha_guard(box, ref_path)
    cov = (np.array(out)[:, :, 3] > 128).mean()
    assert cov < 0.3, f"清底后覆盖度应回落到圆面积附近，实际 {cov:.0%}"
    # 掩膜清底后剪影恒等于参考剪影，IoU 退化为 ~1（由掩膜强制保证一致）
    assert iou > 0.9
    os.remove(ref_path)


def test_variant_alpha_guard_iou_measures_silhouette():
    """P4：IoU 反映剪影差异——同圆高、错开圆低。"""
    from src.engine.godot.asset_forge import _variant_alpha_guard

    ref = _circle_mask_img(512, 0.2, (150, 80, 190, 255))
    ref_path = ".tmp/guard_ref.png"
    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    ref.save(ref_path)

    same = _circle_mask_img(512, 0.2, (220, 20, 60, 255))       # 同剪影换色
    shifted = _circle_mask_img(512, 0.08, (220, 20, 60, 255))   # 小圆（明显不同）

    _, iou_same = _variant_alpha_guard(same, ref_path)
    _, iou_shifted = _variant_alpha_guard(shifted, ref_path)
    assert iou_same > 0.8
    assert iou_shifted < 0.3
    os.remove(ref_path)


def test_generate_one_variant_drift_falls_back(monkeypatch, tmp_path):
    """P4：i2i 变体剪影漂移（IoU 低于阈值）→ _generate_one 返回 None，
    由 forge_assets 的 _hue_variants_if_missing 走色相保底。"""
    import shutil

    from PIL import Image

    from src.engine.godot import asset_forge

    shutil.rmtree(tmp_path / "proj", ignore_errors=True)
    gen = tmp_path / "proj" / "assets" / "gen"
    gen.mkdir(parents=True)
    # 参考图：小圆剪影
    _circle_mask_img(512, 0.12, (150, 80, 190, 255)).save(gen / "enemy.png")

    raw = tmp_path / "raw.png"
    # 模型输出：白底 + 大方块（remove_background 能抠掉白底 → 干净但剪影完全不同）
    big = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    big.paste(Image.new("RGBA", (700, 700), (220, 20, 60, 255)), (162, 162))
    big.save(raw)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def generate_image(self, prompt, size=None, **kw):
            return {"success": True, "image_path": str(raw)}

    monkeypatch.setattr("src.image.ai_image_client.AIImageClient", FakeClient)
    path = asset_forge._generate_one(
        "enemy", str(tmp_path / "proj"), timeout=10,
        prompt="the same enemy creature in a crimson red color scheme",
        reference_paths=[str(gen / "enemy.png")],
        out_key="enemy2", same_subject=True,
    )
    assert path is None, "剪影漂移的 i2i 变体必须被拒绝"
    assert not (gen / "enemy2.png").exists(), "被拒绝的变体不应落盘"


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

        def generate_image(self, prompt, size=None, **kw):
            # kw: genre / palette_base / camera（P1 风格漏斗上下文）
            return {"success": True, "image_path": str(big)}

    monkeypatch.setattr("src.image.ai_image_client.AIImageClient", FakeClient)
    project = tmp_path / "proj"
    path = asset_forge._generate_one("player", str(project), timeout=10)
    assert path == "res://assets/gen/player.png"
    out = Image.open(project / "assets" / "gen" / "player.png")
    assert out.size == (512, 512)


def test_generate_one_i2i_failure_falls_back_to_t2i(monkeypatch, tmp_path):
    """P3：带参考图但 i2i 失败 → 自动去掉参考图重生成一次（锚缺失不丢素材）。"""
    import shutil

    from PIL import Image

    from src.engine.godot import asset_forge

    shutil.rmtree(tmp_path / "proj", ignore_errors=True)  # 确定性 tmp 目录跨运行复用，先清理
    raw = tmp_path / "raw.png"
    Image.new("RGBA", (512, 512), (5, 5, 5, 255)).save(raw)
    ref = tmp_path / "player.png"
    Image.new("RGBA", (64, 64), (10, 120, 40, 255)).save(ref)

    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def generate_image(self, prompt, size=None, **kw):
            calls.append({"prompt": prompt, **kw})
            # 带参考图（i2i）一律失败，模拟 provider 不支持/限流
            if kw.get("reference_image_paths"):
                return {"success": False, "error": "i2i unsupported"}
            return {"success": True, "image_path": str(raw)}

    monkeypatch.setattr("src.image.ai_image_client.AIImageClient", FakeClient)
    project = tmp_path / "proj"
    path = asset_forge._generate_one(
        "npc", str(project), timeout=10, prompt="villager NPC",
        reference_paths=[str(ref)],
    )
    assert path == "res://assets/gen/npc.png"
    # 第一次带参考图（i2i），第二次退回文生图
    assert len(calls) == 2
    assert calls[0].get("reference_image_paths")
    assert not calls[1].get("reference_image_paths")
    # i2i 调用把内容包进了"风格锚"编辑指令
    assert "art-style anchor" in calls[0]["prompt"]
    # 退回文生图时用原始内容 prompt，不包编辑指令
    assert calls[1]["prompt"] == "villager NPC"
