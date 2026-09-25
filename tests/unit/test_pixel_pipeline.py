"""像素化后处理管线单元测试。

覆盖 tests/unit/test_asset_forge.py 未触及的新增路径：
- 降采样 → 量化 → 整数倍放大的产物约束
- 调色板解析（主题调色板 / 自提取）
- _SPEC 的 pixel 与 size 对齐（保证放大是整数倍）
"""

from __future__ import annotations

import pytest

from PIL import Image

from src.image.procedural.pixel_pipeline import (
    THEME_PALETTES,
    palette_for,
    pixelate,
    resolve_native,
)


def _noisy(w=256, h=256, seed=7):
    """造一张"照片感"测试图：大量近似色 + 一处纯色背景。"""
    import random

    rnd = random.Random(seed)
    img = Image.new("RGBA", (w, h), (140, 120, 100, 255))
    px = img.load()
    for y in range(h):
        for x in range(w):
            if 40 < x < w - 40 and 40 < y < h - 40:
                px[x, y] = (
                    60 + rnd.randint(0, 40),
                    110 + rnd.randint(0, 40),
                    70 + rnd.randint(0, 40),
                    255,
                )
    return img


def test_palette_for_known_and_unknown_base():
    forest = palette_for("forest_green")
    assert len(forest) == len(THEME_PALETTES["forest_green"])
    assert all(len(c) == 4 for c in forest)
    # 未知 / 空值回落 default，不抛异常
    assert palette_for("no_such_palette") == palette_for("default")
    assert palette_for(None) == palette_for("default")


def test_resolve_native_returns_exact_divisor():
    # 512 的可行约数中 <= 50 的最大值
    assert resolve_native((50, 50), (512, 512)) == ((32, 32), (512, 512))
    # 已经整除时原样返回
    assert resolve_native((32, 32), (512, 512)) == ((32, 32), (512, 512))
    assert resolve_native((340, 192), (1360, 768)) == ((340, 192), (1360, 768))
    # native 大于 storage 时被夹紧
    assert resolve_native((2048, 2048), (512, 512))[0] == (512, 512)


@pytest.mark.parametrize(
    "native,storage",
    [((32, 32), (512, 512)), ((16, 16), (512, 512)), ((340, 192), (1360, 768))],
)
def test_pixelate_output_size_and_integer_upscale(native, storage):
    """每个原生像素必须被放大成完全一致的 KxK 色块（无亚像素混色）。"""
    src = _noisy(256, 256)
    out = pixelate(src, native=native, storage=storage, palette=palette_for("forest_green"))

    assert out.size == storage
    assert out.mode == "RGBA"

    kx = storage[0] // native[0]
    ky = storage[1] // native[1]
    data = out.load()
    for by in range(native[1]):
        for bx in range(native[0]):
            ref = data[bx * kx, by * ky]
            for dy in range(ky):
                for dx in range(kx):
                    assert data[bx * kx + dx, by * ky + dy] == ref


def test_pixelate_quantizes_to_target_palette():
    """给定调色板后，不透明像素的颜色必须全部落在调色板内，且数量不超上限。"""
    palette = palette_for("lava_red")
    src = _noisy(256, 256)
    out = pixelate(src, native=(32, 32), storage=(128, 128), palette=palette)

    allowed = {(r, g, b) for r, g, b, _ in palette}
    used = {px[:3] for px in out.convert("RGBA").getdata() if px[3] > 0}
    assert used
    assert used <= allowed


def test_pixelate_auto_palette_respects_color_budget():
    """不传调色板时走 median cut 自提取，颜色数受 colors 约束（远低于原始噪声）。"""
    src = _noisy(256, 256)
    assert len(src.convert("RGBA").getcolors(maxcolors=1 << 20)) > 1000

    out = pixelate(src, native=(32, 32), storage=(128, 128), colors=16)
    n_colors = len(out.convert("RGBA").getcolors(maxcolors=1 << 20))
    assert n_colors <= 16


def test_pixelate_binarizes_alpha_and_keeps_transparency():
    """alpha 二值化：不允许出现半透明羽化边（像素画要么实心要么透明）。"""
    src = Image.new("RGBA", (64, 64), (200, 60, 60, 255))
    # 右上角切一块半透明区域
    for y in range(32):
        for x in range(32, 64):
            src.putpixel((x, y), (200, 60, 60, 90))

    out = pixelate(src, native=(16, 16), storage=(64, 64), colors=8)
    alphas = {px[3] for px in out.getdata()}
    assert alphas <= {0, 255}
    assert 0 in alphas and 255 in alphas


def test_pixelate_does_not_darken_edges_from_transparent_fill():
    """预乘 alpha 降采样：透明区不得污染不透明区，纯色区域颜色应基本守恒。"""
    src = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(16, 48):
        for x in range(16, 48):
            src.putpixel((x, y), (240, 200, 60, 255))

    out = pixelate(src, native=(16, 16), storage=(64, 64), colors=8)
    cx, cy = 32, 32
    center = out.getpixel((cx, cy))
    assert center[3] == 255
    # 中心色不应被透明区的黑色拉暗
    assert center[0] > 150 and center[1] > 120


def test_spec_pixel_divides_size_for_every_asset():
    """对齐验收：_SPEC 里每个槽位的 pixel 必须整除 size，否则放大不是整数倍。"""
    from src.engine.godot.asset_forge import _SPEC

    for key, spec in _SPEC.items():
        assert "pixel" in spec, f"{key} 缺少 pixel 原生尺寸"
        pw, ph = spec["pixel"]
        sw, sh = spec["size"]
        assert sw % pw == 0, f"{key} 宽度 {sw} 不是 pixel {pw} 的整数倍"
        assert sh % ph == 0, f"{key} 高度 {sh} 不是 pixel {ph} 的整数倍"
        assert pw < sw and ph < sh, f"{key} 的 pixel 应小于 size（需要放大）"


def test_theme_palette_resolves_from_scene_ir():
    """asset_forge 应能从 scene_ir 解析出主题调色板（palette_base 补漏点）。"""
    from src.engine.godot.asset_forge import _theme_palette

    class _IR:
        theme = "space_night"
        genre = "shooter"

    palette = _theme_palette(_IR())
    assert palette
    # 深空主题必须落到 space_black 调色板，而不是全局通用的 Kenney 16 色
    assert len(palette) == len(THEME_PALETTES["space_black"])


def test_art_director_exposes_palette_base():
    from src.agents.art_director import resolve_palette_base

    class _IR:
        theme = "neon_city"
        genre = "runner"

    assert resolve_palette_base(_IR()) == "neon_purple"


def test_harmonize_preserves_luminance_and_bounds_hue_shift():
    """主题对齐必须保留明度（细节来源），且色相位移不超过上限。"""
    import colorsys

    from src.image.procedural.pixel_pipeline import harmonize_palette

    theme = palette_for("forest_green")
    # 5 级明度的紫色斜坡：明度必须原样保留，色相只允许有界偏移
    ramp = [(140, 60, 180, 255), (110, 45, 140, 255), (80, 30, 100, 255)]
    out = harmonize_palette(ramp, theme, max_hue_shift=18.0, max_sat_scale=0.25)
    assert len(out) == len(ramp)

    for (r0, g0, b0, _), (r1, g1, b1, _) in zip(ramp, out):
        _h0, _s0, v0 = colorsys.rgb_to_hsv(r0 / 255, g0 / 255, b0 / 255)
        h1, _s1, v1 = colorsys.rgb_to_hsv(r1 / 255, g1 / 255, b1 / 255)
        assert abs(v1 - v0) < 0.02, "明度被改动，细节会丢失"
        h0_deg = colorsys.rgb_to_hsv(r0 / 255, g0 / 255, b0 / 255)[0] * 360
        delta = abs(((h1 * 360 - h0_deg + 180) % 360) - 180)
        assert delta <= 18.5, f"色相位移 {delta:.1f}° 超过上限"


def test_harmonize_leaves_neutral_colors_alone():
    """灰阶素材没有色相，主题对齐不应改动它（地面/石块等的保护）。"""
    from src.image.procedural.pixel_pipeline import harmonize_palette

    neutrals = [(128, 128, 128, 255), (64, 64, 64, 255)]
    out = harmonize_palette(neutrals, palette_for("lava_red"))
    for (r0, g0, b0, _), (r1, g1, b1, _) in zip(neutrals, out):
        assert abs(r1 - r0) + abs(g1 - g0) + abs(b1 - b0) <= 2


def test_augment_with_accents_keeps_signature_hue():
    """主题调色板缺紫色时，紫色素材必须能把它补进来（辨识度保护）。"""
    from src.image.procedural.pixel_pipeline import augment_with_accents

    purple = Image.new("RGBA", (32, 32), (150, 50, 200, 255))
    base = palette_for("forest_green")
    merged = augment_with_accents(base, purple, max_extra=4)
    assert len(merged) > len(base)
    assert any(abs(c[0] - 150) < 90 and abs(c[2] - 200) < 90 for c in merged)


def test_augment_with_accents_is_noop_when_in_gamut():
    """素材颜色本就在主题色域内时不应追加颜色（避免调色板膨胀）。"""
    from src.image.procedural.pixel_pipeline import augment_with_accents

    base = palette_for("forest_green")
    in_gamut = Image.new("RGBA", (32, 32), base[5][:3] + (255,))
    assert augment_with_accents(base, in_gamut, max_extra=4) == list(base)


def test_pixel_options_modes(monkeypatch):
    """三种量化模式的参数装配：harmonize（默认）/ theme / auto。"""
    from src.engine.godot import asset_forge

    class _IR:
        theme = "farm"
        genre = "platformer"

    monkeypatch.delenv("GAMEFORGE_PIXEL_PALETTE", raising=False)
    opts = asset_forge._pixel_options(_IR())
    assert opts["palette"] is None and "harmonize" in opts

    monkeypatch.setenv("GAMEFORGE_PIXEL_PALETTE", "theme")
    opts = asset_forge._pixel_options(_IR())
    assert opts["palette"] and opts["accents"] >= 0

    monkeypatch.setenv("GAMEFORGE_PIXEL_PALETTE", "auto")
    opts = asset_forge._pixel_options(_IR())
    assert opts == {"palette": None, "colors": opts["colors"]}


def test_pixel_opts_slot_override_and_background_policy():
    """槽位可覆写全局模式：背景走 theme（层次分离），其余走 harmonize（保细节）。"""
    from src.engine.godot import asset_forge

    theme = asset_forge._theme_palette(type("_IR", (), {"theme": "farm", "genre": "platformer"})())
    assert theme

    # 全局 harmonize + 背景槽位覆写为 theme
    bg = asset_forge._pixel_opts_for("harmonize", theme, "theme", 48)
    assert bg["palette"] == theme and bg["colors"] == 48 and "accents" in bg

    sprite = asset_forge._pixel_opts_for("harmonize", theme, None, None)
    assert sprite["palette"] is None and sprite["harmonize"] == theme

    # 没有主题参考色时任何模式都退化为自提取，不允许 crash
    assert asset_forge._pixel_opts_for("theme", None)["palette"] is None

    # _SPEC 里背景必须显式声明 theme 策略
    assert asset_forge._SPEC["background"]["palette_mode"] == "theme"
