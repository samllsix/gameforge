"""P0 离线验证：把已生成的 AI 素材跑一遍像素化管线，产出对比图。

对比方式刻意模拟"玩家实际看到的画面"：
    原图 → 按 .tscn 里的 scale 缩到显示尺寸（最近邻）→ 放大到 CELL 便于肉眼检查
像素化版本做同样处理。这样一眼能看出"噪点块"与"干净像素画"的差异。

量化参数走 asset_forge 的真实生产路径（_theme_palette / _pixel_opts_for），
保证验证的就是线上逻辑，而不是脚本里另写一套。
只读现有产物，不触发生图、不修改项目文件。
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, r"D:\game_project")

from src.engine.godot.asset_forge import (  # noqa: E402
    _SPEC,
    _pixel_opts_for,
    _theme_palette,
)
from src.image.procedural.pixel_pipeline import pixelate, resolve_native  # noqa: E402

SRC_DIR = r"D:\game_project\projects\GameForge_Project\assets\gen"
OUT_DIR = r"D:\q-temp\pixel_verify"

# .tscn 里写死的显示尺寸（scene_to_godot.py 的 <n>/512 缩放系数）
DISPLAY = {
    "player": 48, "enemy": 28, "npc": 40, "pickup": 20,
    "decoration": 32, "ground": 28, "platform": 28, "icon": 128,
}
CELL = 96


class _IR:
    """该项目是农场平台跳跃（prompt 里的 barn/windmill），落到 farm → forest_green。"""

    theme = "farm"
    genre = "platformer"


def _colors(img: Image.Image) -> int:
    return len(img.convert("RGBA").getcolors(maxcolors=1 << 22) or [])


def _render(img: Image.Image, display: int) -> Image.Image:
    return img.resize((display, display), Image.NEAREST)


def _cell(img: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", (CELL, CELL), (240, 240, 244, 255))
    draw = ImageDraw.Draw(canvas)
    for y in range(0, CELL, 8):
        for x in range(0, CELL, 8):
            if (x // 8 + y // 8) % 2 == 0:
                draw.rectangle([x, y, x + 7, y + 7], fill=(224, 224, 230, 255))
    scale = min(CELL / img.width, CELL / img.height)
    resized = img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.NEAREST
    )
    canvas.alpha_composite(resized, ((CELL - resized.width) // 2, (CELL - resized.height) // 2))
    return canvas


def _run(img, spec, opts):
    native, storage = resolve_native(tuple(spec["pixel"]), tuple(spec["size"]))
    return pixelate(
        img, native=native, storage=storage, dither=bool(spec.get("dither")), **opts
    ), native


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUT_DIR, "prod"), exist_ok=True)
    theme = _theme_palette(_IR())

    header = []
    stats = []
    for key, spec in _SPEC.items():
        if key == "background":
            continue
        src_path = os.path.join(SRC_DIR, f"{key}.png")
        if not os.path.isfile(src_path):
            continue
        original = Image.open(src_path).convert("RGBA")
        display = DISPLAY.get(key, 48)

        # 生产默认：全局 harmonize，槽位可覆写
        prod, native = _run(original, spec, _pixel_opts_for("harmonize", theme, spec.get("palette_mode"), spec.get("colors")))
        # 对照 A：全部硬量化到主题调色板
        all_theme, _ = _run(original, spec, _pixel_opts_for("theme", theme, spec.get("colors")))
        # 对照 B：纯逐图自提取
        auto, _ = _run(original, spec, _pixel_opts_for("auto", None, spec.get("colors")))
        prod.save(os.path.join(OUT_DIR, "prod", f"{key}.png"))

        stats.append((key, native[0], _colors(original), _colors(prod)))
        header.append((key, [
            _cell(_render(original, display)),
            _cell(_render(prod, display)),
            _cell(_render(all_theme, display)),
            _cell(_render(auto, display)),
        ]))

    row_titles = [
        "原图（当前效果）",
        "生产默认 harmonize",
        "对照：全部 theme",
        "对照：纯 auto",
    ]
    sheet_w = len(header) * CELL
    sheet_h = len(row_titles) * (CELL + 18) + 20
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for r, title in enumerate(row_titles):
        y0 = r * (CELL + 18) + 4
        draw.text((4, y0), title, fill=(20, 20, 20, 255))
        for c, (_key, imgs) in enumerate(header):
            sheet.alpha_composite(imgs[r], (c * CELL, y0 + 16))
    for c, (key, _) in enumerate(header):
        draw.text((c * CELL + 4, sheet_h - 14), key, fill=(60, 60, 60, 255))
    sheet.save(os.path.join(OUT_DIR, "sheet_sprites.png"))

    # 背景：生产默认是 theme 模式（大画幅渐变层次分离最好）
    bg_path = os.path.join(SRC_DIR, "background.png")
    if os.path.isfile(bg_path):
        bg = Image.open(bg_path).convert("RGBA")
        spec = _SPEC["background"]
        prod_bg, _ = _run(bg, spec, _pixel_opts_for("harmonize", theme, spec.get("palette_mode"), spec.get("colors")))
        harm_bg, _ = _run(bg, spec, _pixel_opts_for("harmonize", theme, None, spec.get("colors")))
        auto_bg, _ = _run(bg, spec, _pixel_opts_for("auto", None, None, spec.get("colors")))
        prod_bg.save(os.path.join(OUT_DIR, "prod", "background.png"))

        w = 620
        h = int(768 * w / 1360)
        imgs = [("原图", bg), ("生产默认 theme+accents 48色", prod_bg),
                ("对照 harmonize 48色", harm_bg), ("对照 auto 48色", auto_bg)]
        strip = Image.new("RGBA", (w, (h + 14) * len(imgs)), (255, 255, 255, 255))
        d2 = ImageDraw.Draw(strip)
        for i, (label, im) in enumerate(imgs):
            d2.text((4, i * (h + 14)), label, fill=(20, 20, 20, 255))
            strip.alpha_composite(im.convert("RGBA").resize((w, h), Image.LANCZOS), (0, i * (h + 14) + 13))
        strip.save(os.path.join(OUT_DIR, "sheet_background.png"))

    lines = ["asset            native_px   orig_colors   after_colors"]
    for key, npx, c0, c1 in stats:
        lines.append(f"{key:16s} {npx:9d} {c0:13d} {c1:14d}")
    with open(os.path.join(OUT_DIR, "stats.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
