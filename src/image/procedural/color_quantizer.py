"""GameForge - 调色板统一工具

把任意 RGBA 图像量化到给定调色板（≤16 色），保留最接近的索引色。
GameForge 默认调色板：Kenney / 通用像素风 16 色。
"""
from typing import List, Tuple
import numpy as np
from PIL import Image


# GameForge 默认调色板（16 色，RGBA 0-255）
DEFAULT_PALETTE: List[Tuple[int, int, int, int]] = [
    (0, 0, 0, 0),         # 透明
    (20, 20, 28, 255),     # 黑
    (60, 60, 80, 255),     # 深灰
    (120, 120, 140, 255),  # 浅灰
    (220, 220, 230, 255),  # 白
    (180, 60, 60, 255),    # 红
    (240, 140, 60, 255),   # 橙
    (240, 220, 80, 255),   # 黄
    (100, 180, 80, 255),   # 绿
    (60, 140, 200, 255),   # 蓝
    (140, 100, 200, 255),  # 紫
    (200, 140, 200, 255),  # 粉
    (120, 80, 40, 255),    # 棕
    (200, 160, 100, 255),  # 沙
    (100, 200, 200, 255),  # 青
    (40, 100, 80, 255),    # 森绿
]


def quantize_to_palette(
    image: Image.Image, palette: List[Tuple[int, int, int, int]] = None
) -> Image.Image:
    """把图像量化到指定调色板（最近邻匹配）"""
    if palette is None:
        palette = DEFAULT_PALETTE
    arr = np.array(image.convert("RGBA"))
    h, w, _ = arr.shape
    flat = arr.reshape(-1, 4).astype(np.int32)

    pal_arr = np.array(palette, dtype=np.int32)  # (P, 4)
    # 计算每个像素到调色板的欧氏距离（忽略 alpha）
    rgb_pix = flat[:, :3]
    rgb_pal = pal_arr[:, :3]
    # (N, P, 3)
    dist = np.sqrt(((rgb_pix[:, None, :] - rgb_pal[None, :, :]) ** 2).sum(axis=2))
    idx = dist.argmin(axis=1)
    out = pal_arr[idx]
    # 保留原 alpha
    out[:, 3] = flat[:, 3]
    out = out.reshape(h, w, 4).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def hex_to_rgba(hex_color: str) -> Tuple[int, int, int, int]:
    """把 #RGB / #RRGGBB 转 RGBA"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, 255)


def hex_palette(colors: List[str]) -> List[Tuple[int, int, int, int]]:
    """把 hex 列表转 RGBA 列表"""
    return [hex_to_rgba(c) for c in colors]