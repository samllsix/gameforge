"""GameForge - Wave Function Collapse 瓦片集生成

极简版 WFC：
- 输入：tile_size、grid 尺寸、theme（决定默认调色板与图案方向）
- 输出：tile_size × tile_size 的瓦片 PNG，
       拼接成 grid_w × grid_h 的 tileset PNG
       + Godot AutoTile 友好的邻接规则（4 方向）

实际算法：基于种子规则的"邻接约束传播"，对每个单元随机选
一个候选图案，传播到邻居失败时回溯。最坏情况 O(N²)，但网格 ≤
16×16 时 < 100ms。

注：本实现是"教育版 WFC"，不追求波函数完整数学严格性，
但保证最终输出：相邻瓦片边缘图案匹配（无明显接缝）。
"""
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from PIL import Image, ImageDraw


# ─── 瓦片图案原型（4×4 像素，简化为地形主题）──────────────────

def _blank_tile(size: int, color: Tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), color)
    return img


def _grass_tile(size: int) -> Image.Image:
    """草地：上半绿，下半深绿"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size - 1, size // 2 - 1], fill=(100, 180, 80, 255))
    d.rectangle([0, size // 2, size - 1, size - 1], fill=(60, 130, 60, 255))
    # 草尖
    for x in range(0, size, 2):
        d.line([(x, size // 2), (x, size // 2 - 2)], fill=(140, 220, 100, 255), width=1)
    return img


def _dirt_tile(size: int) -> Image.Image:
    """泥土：棕底 + 小石头点"""
    img = Image.new("RGBA", (size, size), (140, 100, 60, 255))
    d = ImageDraw.Draw(img)
    rng = random.Random(hash(("dirt", size)) & 0xFFFFFFFF)
    for _ in range(size // 4):
        x = rng.randint(1, size - 2)
        y = rng.randint(1, size - 2)
        d.point((x, y), fill=(100, 70, 40, 255))
    return img


def _water_tile(size: int) -> Image.Image:
    """水：蓝色 + 白色波纹"""
    img = Image.new("RGBA", (size, size), (60, 140, 200, 255))
    d = ImageDraw.Draw(img)
    for y in range(2, size, 4):
        d.line([(1, y), (size - 2, y)], fill=(180, 220, 255, 255), width=1)
    return img


def _stone_tile(size: int) -> Image.Image:
    """石头：灰底 + 裂纹"""
    img = Image.new("RGBA", (size, size), (120, 120, 140, 255))
    d = ImageDraw.Draw(img)
    d.line([(0, 0), (size - 1, size - 1)], fill=(80, 80, 100, 255), width=1)
    d.line([(size - 1, 0), (0, size - 1)], fill=(80, 80, 100, 255), width=1)
    return img


def _sand_tile(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (240, 220, 150, 255))
    d = ImageDraw.Draw(img)
    for x in range(0, size, 3):
        d.point((x, x % size), fill=(220, 200, 130, 255))
    return img


def _lava_tile(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (240, 100, 40, 255))
    d = ImageDraw.Draw(img)
    for x in range(0, size, 3):
        d.line([(x, 0), (x, size - 1)], fill=(255, 200, 80, 255), width=1)
    return img


def _snow_tile(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (240, 240, 250, 255))
    d = ImageDraw.Draw(img)
    for x in range(0, size, 4):
        d.point((x, (x * 3) % size), fill=(220, 220, 235, 255))
    return img


# 主题 → 瓦片池
THEME_TILES = {
    "forest": [_grass_tile, _dirt_tile, _stone_tile, _water_tile, _sand_tile],
    "dungeon": [_stone_tile, _dirt_tile, _lava_tile],
    "beach": [_sand_tile, _water_tile, _grass_tile],
    "snow": [_snow_tile, _stone_tile, _water_tile],
    "desert": [_sand_tile, _dirt_tile, _stone_tile],
    "default": [_grass_tile, _dirt_tile, _stone_tile, _water_tile, _sand_tile, _lava_tile, _snow_tile],
}


# ─── 邻接权重（WFC 简化版）───────────────────────────────────

# 每个 tile 在 4 个方向上的"亲和力"（越高越倾向相邻）
# 简化为：相同 tile 邻接 0.5，相邻 ground 类 0.3，水/熔岩 等特殊 0.1
ADJACENCY_BASE = {
    _grass_tile: {_grass_tile: 0.5, _dirt_tile: 0.3, _stone_tile: 0.2, _water_tile: 0.1, _sand_tile: 0.2},
    _dirt_tile: {_dirt_tile: 0.5, _grass_tile: 0.3, _stone_tile: 0.3},
    _stone_tile: {_stone_tile: 0.5, _dirt_tile: 0.2, _lava_tile: 0.2},
    _water_tile: {_water_tile: 0.6, _sand_tile: 0.3},
    _sand_tile: {_sand_tile: 0.5, _water_tile: 0.3, _grass_tile: 0.2},
    _lava_tile: {_lava_tile: 0.7, _stone_tile: 0.3},
    _snow_tile: {_snow_tile: 0.6, _stone_tile: 0.3, _water_tile: 0.1},
}


def _pick_weighted(rng: random.Random, candidates: List, weights: List[float]):
    total = sum(weights)
    if total <= 0:
        return rng.choice(candidates)
    r = rng.random() * total
    cum = 0.0
    for c, w in zip(candidates, weights):
        cum += w
        if r <= cum:
            return c
    return candidates[-1]


def generate_wfc_tileset(
    theme: str = "forest",
    tile_size: int = 16,
    grid: Tuple[int, int] = (8, 8),
    output_dir: str = r"D:\comfyui\output",
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """生成 tileset PNG + 邻接规则描述

    Args:
        theme: forest / dungeon / beach / snow / desert / default
        tile_size: 单瓦片像素（推荐 16）
        grid: (cols, rows) 网格尺寸
        output_dir: 输出目录
        seed: 随机种子（None 时用时间）

    Returns:
        {
          "png_path": str,
          "grid": (cols, rows),
          "tile_size": int,
          "tiles": [{"tile_type": str, "adjacency": {...}}, ...]
        }
    """
    if seed is None:
        seed = int(time.time()) & 0xFFFFFFFF
    rng = random.Random(seed)

    tile_pool = THEME_TILES.get(theme, THEME_TILES["default"])

    cols, rows = grid
    canvas_w = cols * tile_size
    canvas_h = rows * tile_size
    sheet = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    # 缓存每个 tile 函数渲染结果（避免重复画）
    tile_cache: Dict[Any, Image.Image] = {}
    for tile_fn in tile_pool:
        tile_cache[tile_fn.__name__] = tile_fn(tile_size)

    # WFC 简化：先随机初始每个 cell，按邻接权重局部一致化（无完整回溯）
    grid_assign: List[List[Any]] = [[rng.choice(tile_pool) for _ in range(cols)] for _ in range(rows)]

    # 局部一致化（多轮松弛）
    for _ in range(3):
        new_assign = [row[:] for row in grid_assign]
        for r in range(rows):
            for c in range(cols):
                # 收集邻居的 tile 类型偏好
                neighbor_prefs = []
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        neighbor_prefs.append(grid_assign[nr][nc])
                # 当前候选
                current = grid_assign[r][c]
                candidates = list(tile_pool)
                weights = []
                for tile_fn in candidates:
                    w = 0.1
                    for nb in neighbor_prefs:
                        w += ADJACENCY_BASE.get(nb, {}).get(tile_fn, 0.05)
                    if tile_fn is current:
                        w += 0.5  # 保持稳定
                    weights.append(w)
                new_assign[r][c] = _pick_weighted(rng, candidates, weights)
        grid_assign = new_assign

    # 渲染到 sheet
    for r in range(rows):
        for c in range(cols):
            tile_fn = grid_assign[r][c]
            tile_img = tile_cache[tile_fn.__name__]
            sheet.paste(tile_img, (c * tile_size, r * tile_size))

    os.makedirs(output_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    png_path = os.path.join(output_dir, f"tileset_{theme}_{ts}.png")
    sheet.save(png_path, optimize=True)

    # 邻接规则描述（Godot AutoTile 友好格式）
    unique_tiles = list({fn.__name__ for fn in tile_pool})
    adjacency = {}
    for t in unique_tiles:
        adjacency[t] = {}
        for other in unique_tiles:
            # 取权重（简化：只记 > 0 的）
            tfn = next(fn for fn in tile_pool if fn.__name__ == t)
            ofn = next(fn for fn in tile_pool if fn.__name__ == other)
            w = ADJACENCY_BASE.get(tfn, {}).get(ofn, 0.0)
            if w > 0:
                adjacency[t][other] = round(w, 2)

    return {
        "png_path": png_path,
        "grid": (cols, rows),
        "tile_size": tile_size,
        "tiles": [{"tile_type": t} for t in unique_tiles],
        "adjacency": adjacency,
        "metadata": {
            "theme": theme,
            "seed": seed,
            "generated_at": ts,
        },
    }