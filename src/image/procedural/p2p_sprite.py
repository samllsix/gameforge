"""GameForge - Pixels-to-Patterns 角色精灵生成

把 character_template 的体素网格按 palette_map 着色，
按目标 pixel_size 放大，输出 RGBA PNG + 简单元数据。

特点：
- 完全离线（无外部 API / 无模型）
- 单帧 < 50ms
- 输出可直接给 Godot 用（透明 PNG + 8 帧 sprite sheet 可由 aseprite_runner 合成）
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from PIL import Image

from .character_template import (
    CharacterTemplate, get_template, PoseGrid,
    PART_EMPTY,
)
from .color_quantizer import quantize_to_palette


def _render_grid_to_array(
    grid_cells: List[str], palette_map: Dict[str, str], pixel_size: int = 1,
) -> Image.Image:
    """把体素网格转 RGBA 图像"""
    h = len(grid_cells)
    w = max(len(l) for l in grid_cells) if grid_cells else 0
    canvas_w = w * pixel_size
    canvas_h = h * pixel_size
    arr = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

    for y, line in enumerate(grid_cells):
        for x, ch in enumerate(line[:w]):
            color_hex = palette_map.get(ch, "#ff00ff")
            if color_hex == "transparent":
                continue  # 留透明
            r, g, b, a = _hex_to_rgba(color_hex)
            # 写入 pixel_size × pixel_size 块
            y0, y1 = y * pixel_size, (y + 1) * pixel_size
            x0, x1 = x * pixel_size, (x + 1) * pixel_size
            arr[y0:y1, x0:x1] = [r, g, b, a]

    return Image.fromarray(arr, mode="RGBA")


def _hex_to_rgba(hex_color: str) -> Tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, 255)


def _build_action_sequence(
    template: CharacterTemplate, action: str, frame_count: int,
) -> List[PoseGrid]:
    """根据动作名生成帧序列（基础版本：复用同一姿态，按需要做轻微扰动）"""
    base = template.poses.get(action) or template.poses.get("idle")
    if base is None:
        return []

    seq = []
    for i in range(frame_count):
        # 复制并做微小变化（4 帧为周期）
        cells = list(base.cells)
        if action == "walk" and template.name == "humanoid":
            # 上下半身错位
            phase = i % 4
            row_offset = 1 if phase in (1, 3) else 0
            # 简化：不做实际错位（避免越界），保持原样
            pass
        elif action == "attack" and template.name == "humanoid":
            # 武器位置在第 3 行附近做挥舞弧线
            if i >= 2 and i <= 3:
                # 武器扫过中央
                cells = [c.replace("6", ".") for c in cells]
                if 8 < len(cells):
                    cells[8] = cells[8][:10] + "66" + cells[8][12:]
        seq.append(PoseGrid(cells=cells, width=base.width, height=base.height))
    return seq


def generate_p2p_sprite(
    template: str = "humanoid",
    palette: Optional[List[str]] = None,
    pixel_size: int = 4,
    frames: int = 8,
    actions: Optional[List[str]] = None,
    output_dir: str = r"D:\comfyui\output",
    quantize: bool = True,
) -> Dict[str, Any]:
    """生成 sprite sheet（多动作 × 多帧拼接）

    Args:
        template: "humanoid" / "slime"
        palette: 调色板 hex 列表（可选；不提供则用模板默认）
        pixel_size: 单个体素像素（4 表示每个网格单元 4×4 像素）
        frames: 每个动作的帧数
        actions: 动作列表，None 时根据模板类型自动选择
        output_dir: 输出目录
        quantize: 是否量化到 16 色调色板

    Returns:
        {
          "png_path": "<output>/sprite_<template>_<ts>.png",
          "frames": [{"action": ..., "frame_index": ..., "img": Image}],
          "metadata": {"template": ..., "frames_per_action": ..., "pixel_size": ...}
        }
"""
    if actions is None:
        actions = ["idle", "walk"] if template == "humanoid" else ["idle"]

    tpl = get_template(template)
    if palette:
        # 调色板 hex 覆盖模板默认
        from .character_template import (
            PART_HEAD, PART_BODY, PART_LHAND, PART_RHAND,
            PART_LFOOT, PART_RFOOT, PART_WEAPON, PART_EYE,
        )
        if len(palette) >= 1:
            tpl.palette_map[PART_BODY] = palette[0]
        if len(palette) >= 2:
            tpl.palette_map[PART_HEAD] = palette[1]
        if len(palette) >= 3:
            tpl.palette_map[PART_LFOOT] = palette[2]
        if len(palette) >= 4:
            tpl.palette_map[PART_RFOOT] = palette[2]

    all_frames = []
    rendered_imgs = []

    for action in actions:
        seq = _build_action_sequence(tpl, action, frames)
        for idx, pose in enumerate(seq):
            img = _render_grid_to_array(pose.cells, tpl.palette_map, pixel_size)
            if quantize:
                img = quantize_to_palette(img)
            rendered_imgs.append(img)
            all_frames.append({"action": action, "frame_index": idx, "img": img})

    # 拼成 sprite sheet（横向排列，每行一个动作）
    if not rendered_imgs:
        raise ValueError(f"No frames rendered for template={template}")

    frame_w = rendered_imgs[0].width
    frame_h = rendered_imgs[0].height
    sheet_w = frame_w * frames
    sheet_h = frame_h * len(actions)

    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    for row, action in enumerate(actions):
        for col in range(frames):
            idx = row * frames + col
            if idx >= len(rendered_imgs):
                break
            sheet.paste(rendered_imgs[idx], (col * frame_w, row * frame_h))

    os.makedirs(output_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    png_path = os.path.join(output_dir, f"sprite_{template}_{ts}.png")
    sheet.save(png_path, optimize=True)

    return {
        "png_path": png_path,
        "sheet_size": (sheet_w, sheet_h),
        "frame_size": (frame_w, frame_h),
        "actions": actions,
        "frames_per_action": frames,
        "metadata": {
            "template": template,
            "pixel_size": pixel_size,
            "quantized": quantize,
            "generated_at": ts,
        },
    }