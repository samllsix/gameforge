"""GameForge - 像素化后处理管线

解决的问题：AI 出图是百万色的"照片"（实测 player.png 有 18,081 个独立颜色），
但项目用最近邻把它硬缩到 20~48px 显示，得到的是"糊掉的噪点块"而不是像素画。

本模块提供确定性后处理：
    预乘 alpha 的 LANCZOS 降采样 → alpha 二值化 → 调色板量化 → NEAREST 整数倍放大

调色板来源二选一：
- 主题调色板（curated，按 palette_base 取）：同一主题下所有素材色彩统一，
  是解决"11 张图各自一套色、拼在一起互相打架"的关键。
- 自提取调色板（median cut）：保留单张素材自身的色彩意图，风险更低。

设计约束：
- 全部为纯函数，无网络、无 LLM、无全局状态
- 任何异常都应被调用方捕获并回退原图（宁要原图不要崩）
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 主题调色板：palette_base -> 十六进制色列表
#
# palette_base 由 src/agents/genre_fusion.py 的 25 个主题包定义，共 6 个取值
# （forest_green / sky_blue / lava_red / neon_purple / warm_beige / space_black），
# 外加 default 兜底。
#
# 每个调色板遵循像素画惯例：深色描边 → 中间调 → 高光，另配 1~2 个强调色。
# 颜色数量控制在 20~28，包含统一的近黑描边，这是"像不像一款游戏"的关键。
# ---------------------------------------------------------------------------

THEME_PALETTES: Dict[str, Tuple[str, ...]] = {
    # 农场 / 丛林 / 中世纪 / 武侠 / 昆虫 —— 草木、木材、暖阳
    "forest_green": (
        "#1b1d17", "#22301c", "#2f4a24", "#3d6b2a", "#4f8a33",
        "#6aa83f", "#8cc44f", "#b0d86a", "#d6e88f",
        "#3a2a1e", "#5c4128", "#7d5a35", "#a3763f", "#c49a5c", "#e0c084",
        "#7ec8e3", "#a8ddef", "#d8f0f8",
        "#f2d06b", "#f7e9a8",
        "#f4f0e6", "#c9c3b4", "#8a8577", "#4c4a42",
    ),
    # 深海 / 雪原 / 空岛 / 童话 / 校园 —— 冷蓝、冰雪、少量暖色点缀
    "sky_blue": (
        "#0b1e33", "#123a5c", "#1b5a86", "#2a7fb0", "#3fa3d4",
        "#6cc4e8", "#a3ddf2", "#d6f0fa",
        "#ffffff", "#eaf6fc", "#c9e6f5",
        "#f2c14e", "#e8935c", "#c9556b",
        "#6b7a86", "#94a3ad", "#c2ccd3",
        "#2f3b4a", "#4f6172",
    ),
    # 地牢 / 火山 / 废土 —— 岩浆红橙 + 灰烬
    "lava_red": (
        "#170d0d", "#2b1512", "#451f16", "#662c1a",
        "#8f3a1d", "#b34e21", "#d4642a", "#e88a3c",
        "#f5a94f", "#fbd07a", "#fff0b3",
        "#3b3634", "#5c5551", "#857c76", "#b3aaa2",
        "#5a2f52", "#f4f0e6",
    ),
    # 赛博霓虹 / 墓地 / 霓虹赛道 —— 紫调 + 青粉霓虹
    "neon_purple": (
        "#0d0a1a", "#1a1230", "#2a1c4a",
        "#432a6b", "#5f3a92", "#7d4fba",
        "#b06cff", "#d79cff", "#f0d6ff",
        "#22d3ee", "#67e8f9",
        "#ff5f9e",
        "#c8c8d8", "#8b8b9e", "#4a4a5c",
    ),
    # 沙漠 / 蒸汽朋克 / 石器 / 西部 / 拉面 / 埃及 / 烘焙 —— 沙土、黄铜、暖光
    "warm_beige": (
        "#2b2116", "#443522", "#5f4a2e",
        "#8a6b3f", "#a8854f", "#c6a065", "#dcbb85",
        "#f0d9ac", "#f8ecc9", "#fdf8e4",
        "#b08434", "#d9a94a",
        "#4f8f8a", "#a8453c",
        "#3d3a34", "#6b675e",
    ),
    # 深空舰桥 —— 冷黑蓝底 + 星光
    "space_black": (
        "#05060f", "#0c1024", "#141a3a",
        "#232c5c", "#35417f", "#4a5aa8",
        "#6f80d4", "#9aa8e8", "#c8d2f8",
        "#ffffff", "#ffe9a8", "#a8f0ff",
        "#ff6b6b", "#6bffb8",
    ),
    # 复古竞技场 / 未知主题 —— Kenney 风格通用 16 色扩充
    "default": (
        "#000000", "#14141c", "#3c3c50", "#78788c", "#dcdee6",
        "#b43c3c", "#f08c3c", "#f0dc50", "#64b450", "#3c8cc8",
        "#8c64c8", "#c88cc8", "#785028", "#c8a064", "#64c8c8",
        "#286450",
    ),
}

# 4x4 有序抖动矩阵（Bayer）。用于给大面积渐变加上复古颗粒感，
# 避免有限调色板量化在天空/渐变区域产生生硬的色带。
_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


def palette_for(palette_base: Optional[str]) -> List[Tuple[int, int, int, int]]:
    """按 palette_base 取主题调色板（未知取值回落 default），返回 RGBA 列表。"""
    from src.image.procedural.color_quantizer import hex_palette

    colors = THEME_PALETTES.get(palette_base or "", THEME_PALETTES["default"])
    return hex_palette(list(colors))


def _downsample(img, native: Tuple[int, int]):
    """预乘 alpha 的 LANCZOS 降采样。

    直接对 RGBA 做 LANCZOS 会让透明区（RGB 常为 0 或残留色）渗进边缘，
    产生黑边/脏边。先按 alpha 预乘再缩放、缩完再还原，可避免。
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(img.convert("RGBA")).astype(np.float32)
    alpha = arr[..., 3:4] / 255.0
    premultiplied = np.concatenate([arr[..., :3] * alpha, arr[..., 3:4]], axis=2)
    small = Image.fromarray(premultiplied.astype(np.uint8), "RGBA").resize(
        native, Image.LANCZOS
    )
    out = np.asarray(small).astype(np.float32)
    out_alpha = out[..., 3:4] / 255.0
    rgb = out[..., :3] / np.maximum(out_alpha, 1e-6)
    rgb = np.where(out_alpha > 0, rgb, 0.0)
    merged = np.concatenate([np.clip(rgb, 0, 255), out[..., 3:4]], axis=2)
    return Image.fromarray(merged.astype(np.uint8), "RGBA")


def _binarize_alpha(img, threshold: int):
    """alpha 二值化：像素画要么全不透明要么全透明，没有半透明羽化边。"""
    import numpy as np
    from PIL import Image

    arr = np.array(img.convert("RGBA"))
    arr[..., 3] = np.where(arr[..., 3] >= threshold, 255, 0)
    return Image.fromarray(arr, "RGBA")


def _extract_palette(img, colors: int) -> List[Tuple[int, int, int, int]]:
    """median cut 从图像自身提取调色板（保留单张素材的色彩意图）。

    透明像素会被填充成不透明区域的平均色，避免在调色板里混进一个伪黑色。
    """
    import numpy as np
    from PIL import Image

    arr = np.array(img.convert("RGBA"))
    mask = arr[..., 3] >= 128
    if mask.any():
        fill = arr[..., :3][mask].mean(axis=0).astype(np.uint8)
    else:
        fill = np.array([0, 0, 0], dtype=np.uint8)
    rgb = np.where(mask[..., None], arr[..., :3], fill).astype(np.uint8)

    quantized = Image.fromarray(rgb, "RGB").quantize(colors=colors, method=Image.MEDIANCUT)
    flat = quantized.getpalette() or []
    # 用 numpy 取实际用到的索引（Image.getdata 在 Pillow 14 会被移除）
    used = sorted(int(i) for i in np.unique(np.array(quantized)))
    palette: List[Tuple[int, int, int, int]] = []
    for idx in used:
        base = idx * 3
        if base + 2 < len(flat):
            palette.append((flat[base], flat[base + 1], flat[base + 2], 255))
    return palette or [(0, 0, 0, 255)]


def _apply_bayer_dither(img, strength: int):
    """4x4 有序抖动：量化前给 RGB 加逐像素偏移，把色带打散成颗粒。"""
    import numpy as np
    from PIL import Image

    arr = np.array(img.convert("RGBA")).astype(np.int16)
    h, w = arr.shape[:2]
    tile = np.array(_BAYER4, dtype=np.int16)
    pattern = np.tile(tile, (h // 4 + 1, w // 4 + 1))[:h, :w]
    offset = ((pattern - 8) * strength // 8)[..., None]
    arr[..., :3] = np.clip(arr[..., :3] + offset, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def augment_with_accents(
    base: Sequence[Tuple[int, int, int, int]],
    img,
    max_extra: int = 6,
    threshold: int = 110,
) -> List[Tuple[int, int, int, int]]:
    """在主题调色板上追加"单图特征色"。

    主题调色板保证全项目色彩统一（描边、光照、中性色），但它未必覆盖某张素材的
    标志性色相——例如 forest_green 里没有紫色，紫色史莱姆就会被刷成绿色。
    本函数只把"明显落在主题色域之外"的颜色补进来（RGB 欧氏距离 > threshold），
    既兜住辨识度，又不会把统一性冲淡。

    返回新的调色板（不修改入参）。
    """
    import numpy as np

    merged: List[Tuple[int, int, int, int]] = list(base)
    if max_extra <= 0 or not merged:
        return merged

    base_rgb = np.array([[c[0], c[1], c[2]] for c in merged], dtype=np.float32)
    candidates = _extract_palette(img, max(2, max_extra * 3))
    added = 0
    for color in candidates:
        rgb = np.array([color[0], color[1], color[2]], dtype=np.float32)
        if float(np.sqrt(((base_rgb - rgb) ** 2).sum(axis=1)).min()) > threshold:
            merged.append(color)
            base_rgb = np.vstack([base_rgb, rgb])
            added += 1
            if added >= max_extra:
                break
    return merged


def _palette_reference(palette: Sequence[Tuple[int, int, int, int]]):
    """求调色板的参考色相（按饱和度加权的圆均值）与平均饱和度。

    中性色（饱和度 < 0.15）不参与——灰阶没有色相，参与会让参考色相失真。
    """
    import colorsys
    import math

    acc_x = acc_y = 0.0
    sats: List[float] = []
    for r, g, b, *_ in palette:
        h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.15:
            continue
        radians = math.radians(h * 360)
        acc_x += math.cos(radians) * s
        acc_y += math.sin(radians) * s
        sats.append(s)
    if not sats:
        return None, None
    return math.degrees(math.atan2(acc_y, acc_x)) % 360, sum(sats) / len(sats)


def harmonize_palette(
    palette: Sequence[Tuple[int, int, int, int]],
    target_palette: Sequence[Tuple[int, int, int, int]],
    max_hue_shift: float = 18.0,
    max_sat_scale: float = 0.25,
) -> List[Tuple[int, int, int, int]]:
    """把单图调色板朝主题调色板做"有界"对齐。

    这是"统一感"与"细节"之间的折中：明度（明暗层次，细节来源）完全保留，
    只把色相朝主题参考色相旋转、饱和度朝主题均值收敛，且都有上限。
    因此紫色史莱姆不会被刷成绿色，只会稍微偏暖/偏冷一点；
    而灰阶素材（地面等）因为没有色相，几乎不受影响。

    返回新的调色板（不修改入参）。
    """
    import colorsys

    ref_hue, ref_sat = _palette_reference(target_palette)
    if ref_hue is None:
        return list(palette)

    out: List[Tuple[int, int, int, int]] = []
    for r, g, b, a in palette:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0.12:
            current = h * 360
            delta = ((ref_hue - current + 180.0) % 360.0) - 180.0
            h = ((current + max(-max_hue_shift, min(max_hue_shift, delta))) % 360.0) / 360.0
            if ref_sat:
                s = max(0.0, min(1.0, s + (ref_sat - s) * max_sat_scale))
        nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
        out.append((round(nr * 255), round(ng * 255), round(nb * 255), a))
    return out


def pixelate(
    img,
    *,
    native: Tuple[int, int],
    storage: Optional[Tuple[int, int]] = None,
    palette: Optional[Sequence[Tuple[int, int, int, int]]] = None,
    colors: int = 24,
    accents: int = 0,
    harmonize: Optional[Sequence[Tuple[int, int, int, int]]] = None,
    dither: bool = False,
    dither_strength: int = 24,
    alpha_threshold: int = 128,
):
    """把一张 AI 出图转成真正的像素画。

    参数：
        img        原始图（任意尺寸 / 模式）
        native     原生像素尺寸，像素画在此分辨率上"作画"，如 (32, 32)
        storage    落盘尺寸，必须是 native 的整数倍；None 则等于 native
        palette    固定目标调色板；None 则从图像自身 median cut 提取 colors 色
        accents    仅当给了 palette 时生效：允许追加的特征色上限（0 = 纯主题色）
        harmonize  参考调色板：保留单图明暗层次，只把色相有界地朝它对齐
        dither     是否加 4x4 有序抖动（大面积渐变建议开）
    返回：
        像素化后的 RGBA 图像，尺寸为 storage
    """
    from PIL import Image

    from src.image.procedural.color_quantizer import quantize_to_palette

    native = (int(native[0]), int(native[1]))
    out_size = (int(storage[0]), int(storage[1])) if storage else native

    small = _downsample(img, native)
    small = _binarize_alpha(small, alpha_threshold)

    if palette is None:
        target = _extract_palette(small, max(2, min(256, colors)))
    else:
        target = list(palette)
        if accents > 0:
            target = augment_with_accents(target, small, max_extra=accents)

    if harmonize:
        target = harmonize_palette(target, harmonize)

    if dither:
        small = _apply_bayer_dither(small, dither_strength)

    quantized = quantize_to_palette(small, target)

    # 全透明像素的 RGB 统一清零：既不参与统计，也避免引入多余的 (RGB, alpha=0) 组合。
    # Godot 侧是最近邻采样 + fix_alpha_border，不存在边缘渗色顾虑。
    import numpy as np

    arr = np.array(quantized)
    arr[arr[..., 3] == 0] = 0
    quantized = Image.fromarray(arr, "RGBA")

    if out_size != native:
        quantized = quantized.resize(out_size, Image.NEAREST)
    return quantized


def resolve_native(
    native: Tuple[int, int], storage: Tuple[int, int]
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """把 native 规整成能整除 storage 的尺寸，保证放大是整数倍。

    例如 native=(50,50) storage=(512,512) 会落回 (32,32)（512 的最大可行约数级别），
    避免非整数放大重新引入不均匀像素块。
    """
    out = []
    for n, s in zip(native, storage):
        n = max(1, min(int(n), int(s)))
        if s % n == 0:
            out.append(n)
            continue
        best = 1
        for candidate in range(1, n + 1):
            if s % candidate == 0:
                best = candidate
        out.append(best)
    return (out[0], out[1]), (int(storage[0]), int(storage[1]))
