"""GameForge - 程序化精灵生成器（Image MCP 主力引擎）

按方案 C 设计：在无 GPU、无网络环境下，提供秒级 sprite sheet / tileset / animation。

导出 4 个工具方法：
- generate_image        : 简单概念图（色块合成，兜底用）
- generate_sprite_sheet : 多动作 × 多帧精灵表（用 p2p_sprite）
- generate_tileset      : 主题瓦片集（用 wfc_tileset）
- generate_animation    : 动画帧序列（用 aseprite_runner 或 p2p_sprite 多帧）
"""
import os
from typing import Any, Dict, List, Optional
import numpy as np
from PIL import Image

from .p2p_sprite import generate_p2p_sprite
from .wfc_tileset import generate_wfc_tileset
from .aseprite_runner import run_aseprite_compose, find_aseprite


DEFAULT_OUTPUT_DIR = r"D:\comfyui\output"


class ProceduralSpriteGenerator:
    """Image MCP 的程序化生成器入口

    所有方法都是同步的（CPU 计算），单次 < 100ms，
    适合在 stdio MCP server 里直接调用。
    """

    def __init__(self, output_dir: str = DEFAULT_OUTPUT_DIR):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._aseprite_path = find_aseprite()

    @property
    def has_aseprite(self) -> bool:
        """Aseprite 是否可用"""
        return self._aseprite_path is not None

    def generate_image(
        self, prompt: str, size: List[int] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """简单概念图（prompt 派生色块 + 几何图案）

        用于：UI 背景、剧情插画、占位图。复杂度低、秒级返回。
        """
        if size is None:
            size = [512, 512]
        w, h = size[0], size[1]

        if seed is None:
            seed = abs(hash(prompt)) % (2**32)
        rng = np.random.default_rng(seed)

        # 主色（由 prompt hash 派生）
        hue = (seed % 360) / 360.0
        bg_color = np.array(_hsv_to_rgb(hue, 0.4, 0.3)) * 255
        fg_color = np.array(_hsv_to_rgb((hue + 0.3) % 1.0, 0.7, 0.8)) * 255

        arr = np.zeros((h, w, 4), dtype=np.uint8)
        # 背景渐变
        for y in range(h):
            t = y / h
            arr[y, :, :3] = (bg_color * (1 - t * 0.3)).astype(np.uint8)
            arr[y, :, 3] = 255

        # 几何图案（圆 + 矩形）
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 6
        for y in range(h):
            for x in range(w):
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                if abs(d - radius) < 2:
                    arr[y, x, :3] = fg_color.astype(np.uint8)

        # 随机散点
        n_dots = max(20, (w * h) // 5000)
        for _ in range(n_dots):
            x = rng.integers(0, w)
            y = rng.integers(0, h)
            arr[y, x, :3] = fg_color.astype(np.uint8)

        img = Image.fromarray(arr, mode="RGBA")
        import time
        ts = int(time.time() * 1000)
        path = os.path.join(self.output_dir, f"concept_{ts}.png")
        img.save(path, optimize=True)

        return {
            "png_path": path,
            "size": [w, h],
            "engine": "procedural_concept",
            "seed": seed,
        }

    def generate_sprite_sheet(
        self,
        character: Optional[Dict[str, Any]] = None,
        template: str = "humanoid",
        palette: Optional[List[str]] = None,
        pixel_size: int = 4,
        frames: int = 8,
        actions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """生成角色 sprite sheet

        Args:
            character: {"template": "humanoid", "palette": [...], "pixel_size": 4, ...}
                       为 None 时用下面的参数
            template: 角色模板（humanoid / slime）
            palette: 调色板 hex 列表
            pixel_size: 单体素像素
            frames: 每动作帧数
            actions: 动作列表
        """
        if character:
            template = character.get("template", template)
            palette = character.get("palette", palette)
            pixel_size = character.get("pixel_size", pixel_size)
            frames = character.get("frames", frames)
            actions = character.get("actions", actions)

        return generate_p2p_sprite(
            template=template,
            palette=palette,
            pixel_size=pixel_size,
            frames=frames,
            actions=actions,
            output_dir=self.output_dir,
            quantize=True,
        )

    def generate_tileset(
        self,
        theme: str = "forest",
        tile_size: int = 16,
        grid: Optional[List[int]] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """生成瓦片集（程序化 + WFC 邻接规则）

        Args:
            theme: forest / dungeon / beach / snow / desert / default
            tile_size: 单瓦片像素（推荐 16）
            grid: [cols, rows] 网格尺寸
            seed: 随机种子
        """
        if grid is None:
            grid = [8, 8]
        return generate_wfc_tileset(
            theme=theme,
            tile_size=tile_size,
            grid=(grid[0], grid[1]),
            output_dir=self.output_dir,
            seed=seed,
        )

    def generate_animation(
        self,
        frames: int = 24,
        base_sprite: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """生成动画帧序列

        优先用 Aseprite CLI；无 Aseprite 时直接复用 base_sprite 或生成占位。
        """
        base_path = None
        if base_sprite and isinstance(base_sprite, dict):
            base_path = base_sprite.get("png_path")
            # base_sprite 可能是 generate_sprite_sheet 的输出
            if base_path is None:
                # 退化方案：先生成一张精灵表，再做动画合成
                base_path = self.generate_sprite_sheet(
                    template=base_sprite.get("template", "humanoid"),
                    frames=base_sprite.get("frames", 8),
                )["png_path"]

        return run_aseprite_compose(
            frames=frames,
            output_dir=self.output_dir,
            base_sprite_path=base_path,
            aseprite_path=self._aseprite_path,
        )


def _hsv_to_rgb(h: float, s: float, v: float) -> List[float]:
    """h, s, v ∈ [0, 1]，返回 RGB ∈ [0, 1]"""
    if s == 0:
        return [v, v, v]
    i = int(h * 6)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i = i % 6
    return [
        [v, t, p, p, q, v][i],
        [q, v, v, t, p, p][i],
        [p, p, q, v, v, t][i],
    ]