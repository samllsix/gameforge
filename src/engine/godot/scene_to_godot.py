"""把 SceneIR 转成富画面 Godot 场景。

设计目标：让 demo 项目在 Godot 4.6 上**真有可看的画面**，而不是静态占位图。
实现要点：
- 多层视差背景（ColorRect + 自滚动 _process 脚本）
- 主角/敌人/道具：Sprite2D + 简单脚本（移动、跳跃、漂浮、旋转）
- 粒子系统（CPUParticles2D）：环境效果（雨、雪、火、星星）
- HUD：得分、计时、暂停提示
- 自动加载 GameManager：跑 _process 让动画动起来

不依赖用户写任何代码——纯模板 + SceneIR 字段填充。
"""

from __future__ import annotations

import os
import json
import re
import textwrap
from typing import Any, Dict, List, Optional

import structlog

from src.agents.scene_ir import SceneIR, EntityIR

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════
# 主题色板（SceneIR.theme → RGBA 调色板）
# ═══════════════════════════════════════════════════════════════

THEMES: Dict[str, Dict[str, Any]] = {
    "sky_blue":    {"sky": [70, 130, 220], "ground": [110, 80, 50], "hud": [240, 245, 255], "accent": [255, 200, 80], "particle": [255, 255, 200]},
    "forest_green":{"sky": [50, 90, 60],   "ground": [80, 50, 30],   "hud": [220, 255, 220], "accent": [255, 220, 100], "particle": [180, 255, 180]},
    "space_black": {"sky": [10, 10, 25],   "ground": [40, 30, 60],   "hud": [180, 200, 255], "accent": [255, 100, 200], "particle": [255, 240, 180]},
    "warm_beige":  {"sky": [220, 200, 170],"ground": [180, 140, 100],"hud": [60, 50, 40],    "accent": [200, 80, 60],  "particle": [255, 220, 160]},
    "neon_purple": {"sky": [40, 20, 80],   "ground": [80, 30, 120],  "hud": [220, 180, 255], "accent": [0, 255, 200],  "particle": [255, 100, 255]},
    "lava_red":    {"sky": [120, 30, 20],  "ground": [60, 20, 10],   "hud": [255, 220, 180], "accent": [255, 220, 80], "particle": [255, 180, 60]},
    "default":     {"sky": [70, 130, 220], "ground": [110, 80, 50],  "hud": [240, 245, 255], "accent": [255, 200, 80], "particle": [255, 255, 200]},
}


def _theme_for(theme: Optional[str], background: Optional[str] = None) -> Dict[str, Any]:
    """优先用 SceneIR.theme，否则从 camera.background 推断"""
    if theme and theme in THEMES:
        return THEMES[theme]
    if background and background in THEMES:
        return THEMES[background]
    return THEMES["default"]


def _rgba(rgb: List[int], a: float = 1.0) -> str:
    return f"Color({rgb[0]/255:.3f}, {rgb[1]/255:.3f}, {rgb[2]/255:.3f}, {a})"


# ═══════════════════════════════════════════════════════════════
# TSCN 节点生成
# ═══════════════════════════════════════════════════════════════


def _node_header(node_name: str, node_type: str, parent: Optional[str] = None, extra_props: str = "") -> str:
    """生成 [node name=X type=Y parent=Z ...] 头

    parent 约定（Godot 4 TSCN 规范）：
    - None 或 "" → 根节点（不写 parent 字段）
    - "." → 根节点的直接子节点
    - "NodeName" → 相对于该节点的子节点
    """
    if not parent:
        return f'[node name="{node_name}" type="{node_type}"{extra_props}]'
    return f'[node name="{node_name}" type="{node_type}" parent="{parent}"{extra_props}]'


# Godot 节点名禁止的字符（含 tscn 头引号内的 `"`, 反斜杠, 换行等）
_TSCN_NAME_BAD = re.compile(r'["\\/:@%.\r\n\t]')


def _sanitize_node_name(name: Any, fallback: str = "GameScene") -> str:
    """把 LLM 给出的 scene_name 清洗成合法 Godot 节点名字符串。

    防止 scene_name 里出现引号/换行/路径分隔符等，把 [node name="..."]
    或 project.godot 的 config/name="..." 字面量截断破坏整个文件。
    """
    cleaned = _TSCN_NAME_BAD.sub("_", str(name or "")).strip()
    return cleaned or fallback


def _ext_resource(idx: int, path: str, type_: str = "Script") -> str:
    """生成 [ext_resource ...] 行"""
    return f'[ext_resource type="{type_}" path="{path}" id="{idx}_{type_.lower()}"]'


# 网格品类：玩法由 grid_runtime.gd 确定性自绘，不走实体节点树
GRID_GENRES = {"snake", "pong", "merge_2048", "breakout", "sokoban", "minesweeper"}


def build_scene_tscn(
    scene_ir: SceneIR,
    width: int = 640,
    height: int = 360,
    assets: Optional[Dict[str, str]] = None,
    layout_seed: int = 0,
) -> str:
    """生成 Godot 4 兼容的 .tscn 文本。

    assets: 可选 {player/enemy/pickup/background: res://assets/gen/*.png}。
    提供素材时用 Sprite2D 真图覆盖色块视觉；不提供时行为与旧版完全一致。
    """
    palette = _theme_for(scene_ir.theme, scene_ir.camera.background if scene_ir.camera else None)
    assets = assets or {}

    # 实体分类（按 role 决定使用什么渲染策略）
    entities: List[EntityIR] = scene_ir.entities
    platforms = [e for e in entities if e.role in ("ground", "platform")]
    players   = [e for e in entities if e.role == "player"]
    enemies   = [e for e in entities if e.role == "enemy"]
    npcs      = [e for e in entities if e.role == "npc"]
    pickups   = [e for e in entities if e.role == "pickup"]
    decorations = [e for e in entities if e.role == "decoration"]
    boundary  = [e for e in entities if e.role == "boundary"]

    # 网格品类（贪吃蛇/Pong/2048/打砖块/推箱子/扫雷）：玩法由 GridGame 节点自绘，
    # 实体蓝图不进场景树（各分区循环自然为空）
    if scene_ir.genre in GRID_GENRES:
        platforms = players = enemies = npcs = pickups = decorations = boundary = []

    # ext_resource 索引：先生成所有外部脚本引用
    # id 约定：1_main, 2_mover, 3_bouncer, 4_rotator, 5_pickup, 6_walker, 7_parallax_bg, 8_hud
    ext_lines: List[str] = []
    used_ids: List[str] = []

    def _ext(path: str, type_: str) -> str:
        idx = len(used_ids) + 1
        used_ids.append(f"{idx}_{type_.lower()}")
        ext_lines.append(f'[ext_resource type="{type_}" path="{path}" id="{idx}_{type_.lower()}"]')
        return f"{idx}_{type_.lower()}"

    # 子脚本（运行时生成）
    mover_script  = "res://addons/gameforge/runtime/mover.gd"
    bouncer_script= "res://addons/gameforge/runtime/bouncer.gd"
    rotator_script= "res://addons/gameforge/runtime/rotator.gd"
    pickup_script = "res://addons/gameforge/runtime/pickup.gd"
    walker_script = "res://addons/gameforge/runtime/walker.gd"
    parallax_script = "res://addons/gameforge/runtime/parallax_bg.gd"
    hud_script    = "res://addons/gameforge/runtime/hud.gd"
    player_script = "res://addons/gameforge/runtime/player.gd"

    ext_mover = _ext(mover_script, "Script")
    ext_bouncer = _ext(bouncer_script, "Script")
    ext_rotator = _ext(rotator_script, "Script")
    ext_pickup = _ext(pickup_script, "Script")
    ext_walker = _ext(walker_script, "Script")
    ext_parallax = _ext(parallax_script, "Script")
    ext_hud = _ext(hud_script, "Script")
    ext_player = _ext(player_script, "Script")
    game_flow_script = "res://addons/gameforge/runtime/game_flow.gd"
    ext_game_flow = _ext(game_flow_script, "Script")

    # AI 素材纹理引用（asset_forge 产出；缺失的角色保持色块视觉）
    ext_bg_tex = _ext(assets["background"], "Texture2D") if "background" in assets else None
    ext_player_tex = _ext(assets["player"], "Texture2D") if "player" in assets else None
    ext_pickup_tex = _ext(assets["pickup"], "Texture2D") if "pickup" in assets else None
    ext_ground_tex = _ext(assets["ground"], "Texture2D") if "ground" in assets else None
    ext_platform_tex = _ext(assets["platform"], "Texture2D") if "platform" in assets else None
    ext_decor_tex = _ext(assets["decoration"], "Texture2D") if "decoration" in assets else None
    ext_npc_tex = _ext(assets["npc"], "Texture2D") if "npc" in assets else None
    # 敌人色相变体轮换（Enemy1/2/3 视觉不重样）
    ext_enemy_texes = [
        _ext(t, "Texture2D") for t in
        (assets.get("enemy"), assets.get("enemy2"), assets.get("enemy3")) if t
    ]

    # ---- 节点构建 ----
    import random as _random

    rng = _random.Random(layout_seed)
    scene_name = _sanitize_node_name(scene_ir.scene_name or "GameScene")
    nodes: List[str] = []
    sub_resources: List[str] = []
    connections: List[str] = []

    def _sub_rect(ident: str, w: int, h: int) -> None:
        sub_resources.append(f'[sub_resource type="RectangleShape2D" id="{ident}"]')
        sub_resources.append(f"size = Vector2({w}, {h})")

    # 0. 根节点：位置偏移让内容居中于默认视口（无相机时视口显示世界 (0,0)-(W,H)）
    nodes.append(_node_header(scene_name, "Node2D", parent=None))
    nodes.append(f"position = Vector2({width // 2}, {height // 4})")

    # 1. 三层视差背景（层节点自带滚动脚本；Deco 子节点周期分布实现无缝循环）
    bg_count = 3
    wrap_period = width + 128
    wrap_left = -width - 64
    for i in range(bg_count):
        layer = f"ParallaxLayer{i+1}"
        nodes.append(_node_header(layer, "Node2D", parent="."))
        nodes.append("script = ExtResource(\"" + ext_parallax + "\")")
        nodes.append(f"speed = {0.3 + i * 0.7:.2f}")
        nodes.append(f"wrap_left = {wrap_left}.0")
        nodes.append(f"wrap_period = {wrap_period}.0")
        # 颜色：sky 渐变到 ground
        mix = i / max(1, bg_count - 1)
        c = [
            int(palette["sky"][0] * (1 - mix) + palette["ground"][0] * mix),
            int(palette["sky"][1] * (1 - mix) + palette["ground"][1] * mix),
            int(palette["sky"][2] * (1 - mix) + palette["ground"][2] * mix),
        ]
        # 背景色块（纯色，无需滚动）
        nodes.append(_node_header(f"BGColor{i+1}", "ColorRect", parent=layer))
        nodes.append("color = " + _rgba(c, 1.0))
        nodes.append(f"offset_left = -{width}")
        nodes.append(f"offset_top = -{height}")
        nodes.append(f"offset_right = {width * 2}")
        nodes.append(f"offset_bottom = {height * 2}")
        nodes.append("mouse_filter = 2")
        # AI 生成的背景图（盖在色块之上、装饰星之下；纹理缺失时色块仍是兜底视觉）
        if ext_bg_tex is not None:
            nodes.append(_node_header(f"BGArt{i+1}", "Sprite2D", parent=layer))
            nodes.append('texture = ExtResource("' + ext_bg_tex + '")')
            nodes.append("centered = false")
            nodes.append(f"position = Vector2({-width}, {-height})")
            # 背景图归一化为 1360x768，铺满色块同尺寸区域（3W x 3H，滚动 wrap 用）
            nodes.append(f"scale = Vector2({width * 3 / 1360:.4f}, {height * 3 / 768:.4f})")
            nodes.append("texture_filter = 1")
        # 装饰星星/云朵：均匀铺满一个周期，滚动 wrap 后无缝衔接
        deco_count = 5 + i
        for j in range(deco_count):
            x = int(wrap_left + (j + 0.5) * wrap_period / deco_count)
            y = (j * 73 + i * 31) % max(1, height // 2)
            sz = 4 + (j * 11 + i * 5) % 12
            nodes.append(_node_header(f"Deco{i+1}_{j+1}", "ColorRect", parent=layer))
            nodes.append("color = " + _rgba(palette["particle"], 0.35 + 0.2 * i))
            nodes.append(f"offset_left = {x}")
            nodes.append(f"offset_top = {y}")
            nodes.append(f"offset_right = {x + sz}")
            nodes.append(f"offset_bottom = {y + sz}")
            nodes.append("mouse_filter = 2")

    # 2. 平台/地面（StaticBody2D + 与视觉对齐的碰撞形状）
    for idx, p in enumerate(platforms):
        nm = f"Platform{idx+1}"
        if p.role == "ground":
            x, y, w, h = -width // 2, height // 2 - 24, width, 24
        else:
            x, y, w, h = -200 + idx * 120 + rng.randint(-40, 40), 100 - idx * 30 + rng.randint(-16, 16), 100, 16
        cx, cy = x + w // 2, y + h // 2
        _sub_rect(f"shape_{nm}", w, h)
        nodes.append(_node_header(nm, "StaticBody2D", parent="."))
        nodes.append(f"position = Vector2({cx}, {cy})")
        nodes.append(_node_header("CollisionShape2D", "CollisionShape2D", parent=nm))
        nodes.append(f'shape = SubResource("shape_{nm}")')
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + _rgba(palette["ground"], 1.0))
        nodes.append(f"offset_left = {-w // 2}")
        nodes.append(f"offset_top = {-h // 2}")
        nodes.append(f"offset_right = {w - w // 2}")
        nodes.append(f"offset_bottom = {h - h // 2}")
        nodes.append("mouse_filter = 2")
        if ext_ground_tex is not None:
            nodes.append(_node_header("GroundTex", "Sprite2D", parent=nm))
            nodes.append('texture = ExtResource("' + ext_ground_tex + '")')
            nodes.append("centered = false")
            nodes.append(f"position = Vector2({-w // 2}, {-h // 2})")
            nodes.append("texture_filter = 1")
            nodes.append("texture_repeat = 2")
            nodes.append("region_enabled = true")
            nodes.append(f"region_rect = Rect2(0, 0, {w}, {h})")

    # 3. 主角（CharacterBody2D + 碰撞形状 + 弹跳视觉）
    _sub_rect("shape_player", 32, 48)
    for _idx, p in enumerate(players):
        # 多个玩家实体时节点名必须互不相同，否则 .tscn 出现重名节点、场景解析失败
        pnm = "Player" if _idx == 0 else f"Player{_idx + 1}"
        nodes.append(_node_header(pnm, "CharacterBody2D", parent=".", extra_props=' groups=["player"]'))
        nodes.append(f"position = Vector2({-width // 2 + 100}, {height // 2 - 80})")
        nodes.append("script = ExtResource(\"" + ext_player + "\")")
        nodes.append("speed = 120.0")
        nodes.append("jump_velocity = -260.0")
        nodes.append("gravity = 600.0")
        nodes.append(_node_header("CollisionShape2D", "CollisionShape2D", parent=pnm))
        nodes.append('shape = SubResource("shape_player")')
        nodes.append(_node_header("PlayerVisual", "ColorRect", parent=pnm))
        # 有 AI 精灵时色块设为全透明（仅保留动画脚本载体作用），精灵盖在其上
        nodes.append("color = " + (_rgba([255, 255, 255], 0.0) if ext_player_tex else _rgba(palette["accent"], 1.0)))
        nodes.append("offset_left = -16")
        nodes.append("offset_top = -24")
        nodes.append("offset_right = 16")
        nodes.append("offset_bottom = 24")
        nodes.append("script = ExtResource(\"" + ext_bouncer + "\")")
        nodes.append("bounce_height = 6.0")
        nodes.append("bounce_speed = 2.5")
        nodes.append("mouse_filter = 2")
        # AI 玩家精灵（ColorRect 保留作动画脚本载体，精灵盖在其上）
        if ext_player_tex is not None:
            nodes.append(_node_header("Sprite", "Sprite2D", parent="PlayerVisual"))
            nodes.append('texture = ExtResource("' + ext_player_tex + '")')
            # Control 子节点坐标原点在左上角，矩形 32x48 → 中心 (16, 24)
            nodes.append("position = Vector2(16, 24)")
            nodes.append(f"scale = Vector2({48 / 512:.4f}, {48 / 512:.4f})")
            nodes.append("texture_filter = 1")

    # 4. 敌人（巡逻）
    for idx, e in enumerate(enemies):
        nm = f"Enemy{idx+1}"
        nodes.append(_node_header(nm, "CharacterBody2D", parent=".", extra_props=' groups=["enemy"]'))
        nodes.append(f"position = Vector2({width // 2 - 80 + idx * 60}, {height // 2 - 60})")
        nodes.append("script = ExtResource(\"" + ext_walker + "\")")
        nodes.append("speed = 50.0")
        nodes.append("range = 80.0")
        nodes.append("color_seed = " + str(idx * 31))
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + (_rgba([255, 255, 255], 0.0) if ext_enemy_texes else _rgba([220, 80, 80], 1.0)))
        nodes.append("offset_left = -14")
        nodes.append("offset_top = -14")
        nodes.append("offset_right = 14")
        nodes.append("offset_bottom = 14")
        nodes.append("mouse_filter = 2")
        if ext_enemy_texes:
            nodes.append(_node_header("Sprite", "Sprite2D", parent=nm + "/Visual"))
            nodes.append('texture = ExtResource("' + ext_enemy_texes[idx % len(ext_enemy_texes)] + '")')
            nodes.append("position = Vector2(14, 14)")
            nodes.append(f"scale = Vector2({28 / 512:.4f}, {28 / 512:.4f})")
            nodes.append("texture_filter = 1")

    # 5. NPC（绕中心旋转）
    for idx, n in enumerate(npcs):
        nm = f"NPC{idx+1}"
        nodes.append(_node_header(nm, "Node2D", parent="."))
        nodes.append(f"position = Vector2({-100 + idx * 200}, {80 - idx * 40})")
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + _rgba(palette["accent"], 0.8))
        nodes.append("offset_left = -20")
        nodes.append("offset_top = -20")
        nodes.append("offset_right = 20")
        nodes.append("offset_bottom = 20")
        nodes.append("pivot_offset = Vector2(20, 20)")
        nodes.append("script = ExtResource(\"" + ext_rotator + "\")")
        nodes.append("rot_speed = 1.2")
        nodes.append("mouse_filter = 2")
        if ext_npc_tex is not None:
            nodes.append(_node_header("Sprite", "Sprite2D", parent=nm + "/Visual"))
            nodes.append('texture = ExtResource("' + ext_npc_tex + '")')
            nodes.append("position = Vector2(20, 20)")
            nodes.append(f"scale = Vector2({40 / 512:.4f}, {40 / 512:.4f})")
            nodes.append("texture_filter = 1")

    # 6. 道具（漂浮金币，玩家碰到收集）
    for idx, pk in enumerate(pickups):
        nm = f"Pickup{idx+1}"
        nodes.append(_node_header(nm, "Area2D", parent="."))
        nodes.append(f"position = Vector2({-200 + idx * 80 + rng.randint(-30, 30)}, {60 + (idx % 2) * 40 + rng.randint(-16, 16)})")
        nodes.append("script = ExtResource(\"" + ext_pickup + "\")")
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + (_rgba([255, 255, 255], 0.0) if ext_pickup_tex else _rgba([255, 220, 60], 1.0)))
        nodes.append("offset_left = -10")
        nodes.append("offset_top = -10")
        nodes.append("offset_right = 10")
        nodes.append("offset_bottom = 10")
        nodes.append("pivot_offset = Vector2(10, 10)")
        nodes.append("script = ExtResource(\"" + ext_mover + "\")")
        nodes.append("move_range_x = 60.0")
        nodes.append("move_range_y = 12.0")
        nodes.append("move_speed = 1.5")
        nodes.append("phase = " + str(idx * 0.7))
        nodes.append("mouse_filter = 2")
        if ext_pickup_tex is not None:
            nodes.append(_node_header("Sprite", "Sprite2D", parent=nm + "/Visual"))
            nodes.append('texture = ExtResource("' + ext_pickup_tex + '")')
            nodes.append("position = Vector2(10, 10)")
            nodes.append(f"scale = Vector2({20 / 512:.4f}, {20 / 512:.4f})")
            nodes.append("texture_filter = 1")
        connections.append(
            f'[connection signal="body_entered" from="{nm}" to="{nm}" method="_on_body_entered"]'
        )

    # 7. 装饰物（树/石头，立在地面附近）
    for idx, d in enumerate(decorations):
        nm = f"Prop{idx+1}"
        nodes.append(_node_header(nm, "Node2D", parent="."))
        nodes.append(f"position = Vector2({-width // 2 + 60 + idx * 90 + rng.randint(-24, 24)}, {height // 2 - 40 - (idx % 3) * 24 + rng.randint(-8, 8)})")
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + _rgba(palette["ground"], 0.9))
        nodes.append("offset_left = -8")
        nodes.append("offset_top = -16")
        nodes.append("offset_right = 8")
        nodes.append("offset_bottom = 16")
        nodes.append("mouse_filter = 2")
        if ext_decor_tex is not None:
            nodes.append(_node_header("Sprite", "Sprite2D", parent=nm))
            nodes.append('texture = ExtResource("' + ext_decor_tex + '")')
            nodes.append("position = Vector2(8, 16)")
            nodes.append(f"scale = Vector2({32 / 512:.4f}, {32 / 512:.4f})")
            nodes.append("texture_filter = 1")

    # 8. 粒子系统（环境效果：雨/雪/星屑）
    particles_count = 24 if scene_ir.genre in ("platformer", "shooter", "runner") else 12
    nodes.append(_node_header("Particles", "CPUParticles2D", parent="."))
    nodes.append(f"position = Vector2(0, {height // 4})")
    nodes.append("amount = " + str(particles_count))
    nodes.append("lifetime = 4.0")
    nodes.append("emission_shape = 3")  # EmissionShape.BOX
    nodes.append("emission_rect_extents = Vector2(" + str(width // 2) + ", 20)")
    nodes.append("direction = Vector2(0, 1)")
    nodes.append("spread = 30.0")
    nodes.append("gravity = Vector2(0, 30)")
    nodes.append("initial_velocity_min = 20.0")
    nodes.append("initial_velocity_max = 50.0")
    nodes.append("color = " + _rgba(palette["particle"], 0.6))

    # 9. HUD 层（CanvasLayer，屏幕空间）：HudRoot 兄弟节点直接挂在 HUD 下，避免 parse-order "vanish"
    nodes.append(_node_header("HUD", "CanvasLayer", parent="."))
    nodes.append("layer = 10")
    nodes.append(_node_header("HudRoot", "Control", parent="HUD"))
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("mouse_filter = 2")
    nodes.append("script = ExtResource(\"" + ext_hud + "\")")
    nodes.append("theme_color = " + _rgba(palette["hud"], 1.0))
    # 分数标签（直接挂 HUD，配合 HudRoot 全屏 Control 让 HUD 层的两个标签天然在右上/左上）
    nodes.append(_node_header("ScoreLabel", "Label", parent="HUD"))
    nodes.append("offset_left = 12")
    nodes.append("offset_top = 8")
    nodes.append("offset_right = 200")
    nodes.append("offset_bottom = 32")
    nodes.append("text = \"SCORE 0\"")
    nodes.append("theme_override_colors/font_color = " + _rgba(palette["hud"], 1.0))
    nodes.append("mouse_filter = 2")
    # 计时标签
    nodes.append(_node_header("TimeLabel", "Label", parent="HUD"))
    nodes.append("anchor_left = 1.0")
    nodes.append("anchor_right = 1.0")
    nodes.append("offset_left = -120")
    nodes.append("offset_top = 8")
    nodes.append("offset_right = -12")
    nodes.append("offset_bottom = 32")
    nodes.append("text = \"00:00\"")
    nodes.append("theme_override_colors/font_color = " + _rgba(palette["hud"], 1.0))
    nodes.append("horizontal_alignment = 2")
    nodes.append("mouse_filter = 2")

    # 9.5 网格游戏运行时（确定性玩法，LLM 零参与）
    if scene_ir.genre in GRID_GENRES:
        grid_script = "res://addons/gameforge/runtime/grid_runtime.gd"
        ext_grid = _ext(grid_script, "Script")
        nodes.append(_node_header("GridGame", "Node2D", parent="."))
        # 抵消根节点的居中偏移，让网格棋盘落在视口正中
        nodes.append(f"position = Vector2({-width // 2}, {-height // 4})")
        nodes.append('mode = "' + scene_ir.genre + '"')
        nodes.append("script = ExtResource(\"" + ext_grid + "\")")

    # 10. 游戏流程层（开始画面 / 暂停 / 结束重开 —— 上线必备件）
    nodes.append(_node_header("GameFlow", "CanvasLayer", parent=".", extra_props=' groups=["game_flow"]'))
    nodes.append("process_mode = 3")  # PROCESS_MODE_ALWAYS：树暂停时仍响应输入
    nodes.append("layer = 20")
    nodes.append("script = ExtResource(\"" + ext_game_flow + "\")")

    # 开始画面（点击/回车开始）
    nodes.append(_node_header("BootPanel", "ColorRect", parent="GameFlow"))
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("grow_horizontal = 2")
    nodes.append("grow_vertical = 2")
    nodes.append("color = Color(0, 0, 0, 0.65)")
    nodes.append("mouse_filter = 2")  # 让点击落到 _unhandled_input
    nodes.append(_node_header("Title", "Label", parent="GameFlow/BootPanel"))
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("offset_bottom = -60.0")
    nodes.append("text = \"点击开始游戏\"")
    nodes.append("horizontal_alignment = 1")
    nodes.append("vertical_alignment = 1")
    nodes.append("theme_override_font_sizes/font_size = 36")
    nodes.append("theme_override_colors/font_color = Color(1, 1, 1, 1)")
    nodes.append(_node_header("Help", "Label", parent="GameFlow/BootPanel"))
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("offset_top = 40.0")
    nodes.append("text = \"方向键 / A D 移动 · 空格跳跃 · Esc 暂停\"")
    nodes.append("horizontal_alignment = 1")
    nodes.append("vertical_alignment = 1")
    nodes.append("theme_override_font_sizes/font_size = 18")
    nodes.append("theme_override_colors/font_color = Color(1, 1, 1, 0.75)")

    # 暂停面板
    nodes.append(_node_header("PausePanel", "ColorRect", parent="GameFlow"))
    nodes.append("visible = false")
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("color = Color(0, 0, 0, 0.55)")
    nodes.append(_node_header("PauseLabel", "Label", parent="GameFlow/PausePanel"))
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("text = \"已暂停 · 按 Esc 继续\"")
    nodes.append("horizontal_alignment = 1")
    nodes.append("vertical_alignment = 1")
    nodes.append("theme_override_font_sizes/font_size = 30")

    # 结束面板（重开 / 退出）
    nodes.append(_node_header("OverPanel", "ColorRect", parent="GameFlow"))
    nodes.append("visible = false")
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append("color = Color(0, 0, 0, 0.75)")
    nodes.append(_node_header("Center", "CenterContainer", parent="GameFlow/OverPanel"))
    nodes.append("anchors_preset = 15")
    nodes.append("anchor_right = 1.0")
    nodes.append("anchor_bottom = 1.0")
    nodes.append(_node_header("Box", "VBoxContainer", parent="GameFlow/OverPanel/Center"))
    nodes.append("theme_override_constants/separation = 16")
    nodes.append(_node_header("OverTitle", "Label", parent="GameFlow/OverPanel/Center/Box"))
    nodes.append("text = \"GAME OVER\"")
    nodes.append("horizontal_alignment = 1")
    nodes.append("theme_override_font_sizes/font_size = 40")
    nodes.append("theme_override_colors/font_color = Color(1, 0.35, 0.35, 1)")
    nodes.append(_node_header("ScoreResult", "Label", parent="GameFlow/OverPanel/Center/Box"))
    nodes.append("text = \"最终得分  0\"")
    nodes.append("horizontal_alignment = 1")
    nodes.append("theme_override_font_sizes/font_size = 22")
    nodes.append(_node_header("RestartBtn", "Button", parent="GameFlow/OverPanel/Center/Box"))
    nodes.append("text = \"重新开始 (R)\"")
    nodes.append(_node_header("QuitBtn", "Button", parent="GameFlow/OverPanel/Center/Box"))
    nodes.append("text = \"退出游戏\"")
    connections.append(
        '[connection signal="pressed" from="GameFlow/OverPanel/Center/Box/RestartBtn" to="GameFlow" method="_on_restart_pressed"]'
    )
    connections.append(
        '[connection signal="pressed" from="GameFlow/OverPanel/Center/Box/QuitBtn" to="GameFlow" method="_on_quit_pressed"]'
    )

    # ---- 组装 .tscn 文本（Godot 4 场景用 format=3）----
    parts = [
        "[gd_scene format=3]",
        "",
        *ext_lines,
        "",
        *sub_resources,
        "",
        *nodes,
        "",
        *connections,
        "",
    ]
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 运行时脚本生成
# ═══════════════════════════════════════════════════════════════


RUNTIME_SCRIPTS: Dict[str, str] = {
    "mover.gd": '''extends ColorRect
## 漂浮/摇晃动画（_process 平滑摆动）
@export var move_range_x: float = 60.0
@export var move_range_y: float = 12.0
@export var move_speed: float = 1.5
@export var phase: float = 0.0

var _origin: Vector2

func _ready() -> void:
    _origin = position

func _process(delta: float) -> void:
    var t: float = _t() + phase
    position.x = _origin.x + sin(t * move_speed) * move_range_x
    position.y = _origin.y + cos(t * move_speed * 0.7) * move_range_y

func _t() -> float:
    return float(Time.get_ticks_msec()) / 1000.0
''',

    "bouncer.gd": '''extends ColorRect
## 上下小弹跳
@export var bounce_height: float = 6.0
@export var bounce_speed: float = 2.5

var _origin: float

func _ready() -> void:
    _origin = position.y

func _process(delta: float) -> void:
    var t: float = float(Time.get_ticks_msec()) / 1000.0
    position.y = _origin + abs(sin(t * bounce_speed)) * -bounce_height
''',

    "rotator.gd": '''extends ColorRect
## 持续旋转 + 微微缩放
@export var rot_speed: float = 1.5

func _process(delta: float) -> void:
    rotation += rot_speed * delta
    var s: float = 1.0 + 0.1 * sin(float(Time.get_ticks_msec()) / 300.0)
    scale = Vector2(s, s)
''',

    "pickup.gd": '''extends Area2D
## 道具：自动漂浮 + 旋转 + 玩家碰到后消失并计分
@export var collected: bool = false
@export var score_value: int = 10

func _on_body_entered(body: Node) -> void:
    if collected: return
    if body.name == "Player" or body.is_in_group("player"):
        collected = true
        _sfx("coin")
        var gf := get_tree().get_first_node_in_group("game_flow")
        if gf:
            gf.add_score(score_value)
        var tween := create_tween()
        tween.set_parallel(true)
        tween.tween_property(self, "scale", Vector2(1.8, 1.8), 0.15)
        tween.tween_property(self, "modulate:a", 0.0, 0.2)
        tween.chain().tween_callback(queue_free)

func _sfx(n: String) -> void:
    var stream := load("res://assets/sfx/" + n + ".wav")
    if stream == null:
        return
    var p := AudioStreamPlayer.new()
    p.stream = stream
    add_child(p)
    p.finished.connect(p.queue_free)
    p.play()
''',

    "walker.gd": '''extends CharacterBody2D
## 敌人左右巡逻
@export var speed: float = 50.0
@export var range: float = 80.0
@export var color_seed: int = 0

var _origin: float
var _dir: float = 1.0

func _ready() -> void:
    _origin = position.x
    modulate = Color.from_hsv(fmod(float(color_seed) * 0.1, 1.0), 0.8, 1.0)

func _physics_process(delta: float) -> void:
    position.x += speed * _dir * delta
    if position.x > _origin + range:
        _dir = -1.0
    elif position.x < _origin - range:
        _dir = 1.0
    velocity = Vector2(speed * _dir, 0)
''',

    "parallax_bg.gd": '''extends Node2D
## 视差层：整体向左滚动 Deco* 子节点，越界后按周期 wrap 回右侧，无缝循环
@export var speed: float = 1.0
@export var wrap_left: float = -704.0
@export var wrap_period: float = 768.0

func _process(delta: float) -> void:
	var dx: float = speed * 30.0 * delta
	for c in get_children():
		if c.name.begins_with("Deco"):
			c.position.x -= dx
			if c.position.x < wrap_left:
				c.position.x += wrap_period
''',

    "game_flow.gd": '''extends CanvasLayer
## 游戏流程中枢：开始画面 / Esc 暂停 / 游戏结束与重开 / 计分。
## 进程模式 ALWAYS：树暂停时仍能响应输入（暂停面板、重开）。
## UI 面板（BootPanel/PausePanel/OverPanel）由 build_scene_tscn 以兄弟结构生成。

var score: int = 0
var _started: bool = false
var _over: bool = false

func _ready() -> void:
    # 开局先挂起：等玩家点击/按键开始，计时也从这里起步
    get_tree().paused = true
    _set_visible("BootPanel", true)
    _set_visible("PausePanel", false)
    _set_visible("OverPanel", false)
    _sync_score()
    # 预览/截图模式：自动开始（否则截图永远停在开始画面，玩家无法点击）
    if OS.get_environment("GAMEFORGE_PREVIEW_AUTOSTART").to_lower().strip_edges() == "1":
        start_game()

func _unhandled_input(event: InputEvent) -> void:
    var key := event as InputEventKey
    if key and key.pressed and not key.echo:
        if not _started and (key.keycode == KEY_SPACE or key.keycode == KEY_ENTER):
            start_game()
        elif _over and key.keycode == KEY_R:
            restart()
        elif _started and not _over and key.keycode == KEY_ESCAPE:
            toggle_pause()
        return
    var mb := event as InputEventMouseButton
    if mb and mb.pressed and not _started:
        start_game()

func start_game() -> void:
    if _started:
        return
    _started = true
    _set_visible("BootPanel", false)
    _sfx("click")
    get_tree().paused = false

func toggle_pause() -> void:
    var paused := not get_tree().paused
    get_tree().paused = paused
    _set_visible("PausePanel", paused)

func add_score(v: int) -> void:
    score += v
    _sync_score()

func on_game_over(win: bool = false) -> void:
    if _over:
        return
    _over = true
    get_tree().paused = true
    _sync_score()
    var result: Label = get_node_or_null("OverPanel/Center/Box/ScoreResult") as Label
    if result:
        result.text = "最终得分  %d" % score
    var title: Label = get_node_or_null("OverPanel/Center/Box/OverTitle") as Label
    if title:
        title.text = "YOU WIN!" if win else "GAME OVER"
        title.add_theme_color_override("font_color",
            Color(0.4, 1.0, 0.5) if win else Color(1, 0.35, 0.35))
    _set_visible("OverPanel", true)

func restart() -> void:
    get_tree().paused = false
    get_tree().reload_current_scene()

func _on_restart_pressed() -> void:
    restart()

func _on_quit_pressed() -> void:
    get_tree().quit()

func _set_visible(panel: String, visible_now: bool) -> void:
    var p := get_node_or_null(panel)
    if p:
        p.visible = visible_now

func _sync_score() -> void:
    var scene := get_tree().current_scene
    if scene == null:
        return
    var sl: Label = scene.get_node_or_null("HUD/ScoreLabel") as Label
    if sl:
        sl.text = "SCORE " + str(score)

func _sfx(n: String) -> void:
    var stream := load("res://assets/sfx/" + n + ".wav")
    if stream == null:
        return
    var p := AudioStreamPlayer.new()
    p.stream = stream
    add_child(p)
    p.finished.connect(p.queue_free)
    p.play()
''',
    "hud.gd": '''extends Control
## HUD：计时显示（分数由 GameFlow 节点统一管理，避免两处写同一 Label）
@export var theme_color: Color = Color.WHITE

var _t0: int = 0

func _ready() -> void:
    _t0 = Time.get_ticks_msec()
    set_process(true)

func _process(_delta: float) -> void:
    if get_tree().paused:
        return
    var elapsed: int = Time.get_ticks_msec() - _t0
    var sec: int = int(elapsed / 1000)
    var mm: String = "%02d" % int(sec / 60)
    var ss: String = "%02d" % int(sec % 60)
    var hud: Node = get_parent()
    var tl: Label = hud.get_node_or_null("TimeLabel") as Label
    if tl:
        tl.text = mm + ":" + ss
''',

    "player.gd": '''extends CharacterBody2D
## 玩家：键盘输入控制 + 重力跳跃 + 死亡判定（坠出屏幕 / 触碰敌人）
@export var speed: float = 120.0
@export var jump_velocity: float = -260.0
@export var gravity: float = 600.0
@export var fall_limit: float = 420.0

signal died

var _dead: bool = false

func _physics_process(delta: float) -> void:
    if _dead:
        return
    var dir := Input.get_axis("ui_left", "ui_right")
    velocity.x = dir * speed
    if is_on_floor() and Input.is_action_just_pressed("ui_accept"):
        velocity.y = jump_velocity
        _sfx("jump")
    velocity.y += gravity * delta
    move_and_slide()
    # 触碰敌人（按命名约定 Enemy*）即死亡
    for i in get_slide_collision_count():
        var col := get_slide_collision(i).get_collider()
        if col is Node and (col as Node).name.begins_with("Enemy"):
            _die()
            return
    # 坠出屏幕底部
    if position.y > fall_limit:
        _die()
    # 横向 wrap 保持关卡连续
    if position.x > 320.0:
        position.x = -320.0
    elif position.x < -320.0:
        position.x = 320.0

func _die() -> void:
    if _dead:
        return
    _dead = true
    _sfx("death")
    visible = false
    set_physics_process(false)
    var gf := get_tree().get_first_node_in_group("game_flow")
    if gf:
        gf.on_game_over()

func _sfx(n: String) -> void:
    var stream := load("res://assets/sfx/" + n + ".wav")
    if stream == null:
        return
    var p := AudioStreamPlayer.new()
    p.stream = stream
    add_child(p)
    p.finished.connect(p.queue_free)
    p.play()

''',

    "grid_runtime.gd": '''extends Node2D
## GameForge 网格游戏确定性运行时 —— 贪吃蛇 / Pong / 2048 / 打砖块 / 推箱子 / 扫雷
## 一个脚本覆盖六个品类：LLM 只决定 mode 参数，玩法零生成、零修改。
## 胜负通过 game_flow（组 "game_flow"）上报；分数增量上报。

@export var mode: String = "snake"

const COL_SCORE_COIN := Color(1.0, 0.85, 0.3)
const COL_FG := Color(0.92, 0.95, 1.0)
const COL_BG_DIM := Color(0.08, 0.1, 0.16, 0.85)
const COL_ACCENT := Color(0.4, 0.85, 1.0)
const COL_DANGER := Color(1.0, 0.4, 0.4)

var _gf: Node = null
var _sent_score: int = 0
var _finished: bool = false

# ── 通用小工具 ────────────────────────────────────────────────

func _ready() -> void:
	_gf = get_tree().get_first_node_in_group("game_flow")
	randomize()
	match mode:
		"snake": _init_snake()
		"pong": _init_pong()
		"merge_2048": _init_2048()
		"breakout": _init_breakout()
		"sokoban": _init_sokoban()
		"minesweeper": _init_mines()
		_: _init_snake()

func _process(delta: float) -> void:
	if _finished:
		return
	match mode:
		"snake": _tick_snake(delta)
		"pong": _tick_pong(delta)
		"breakout": _tick_breakout(delta)
	queue_redraw()

func _draw() -> void:
	match mode:
		"snake": _draw_snake()
		"pong": _draw_pong()
		"merge_2048": _draw_2048()
		"breakout": _draw_breakout()
		"sokoban": _draw_sokoban()
		"minesweeper": _draw_mines()

func _unhandled_input(event: InputEvent) -> void:
	if _finished:
		return
	var key := event as InputEventKey
	if key and key.pressed and not key.echo:
		match mode:
			"snake": _key_snake(key.keycode)
			"merge_2048": _key_2048(key.keycode)
			"breakout": _key_breakout(key.keycode)
			"sokoban": _key_sokoban(key.keycode)
			"minesweeper": _key_mines(key.keycode)
			"pong": _key_pong(key.keycode)
		return
	var mb := event as InputEventMouseButton
	if mb and mb.pressed and mode == "minesweeper":
		_click_mines(mb)

func _board_origin() -> Vector2:
	var vp := get_viewport_rect().size
	return Vector2((vp.x - _board_w()) / 2.0, (vp.y - _board_h()) / 2.0)

func _award(points: int) -> void:
	if points <= 0:
		return
	_sent_score += points
	if _gf:
		_gf.add_score(points)

func _end(win: bool) -> void:
	if _finished:
		return
	_finished = true
	if _gf:
		_gf.on_game_over(win)

func _rect(cell: Vector2i, size: float, origin: Vector2, color: Color) -> void:
	draw_rect(Rect2(origin + Vector2(cell.x * size, cell.y * size), Vector2(size, size)), color)

func _label_center(pos: Vector2, size: Vector2, text: String, font_size: int, color: Color) -> void:
	var font := ThemeDB.fallback_font
	draw_string(font, pos + Vector2(0, size.y / 2 + font_size / 2.0), text,
		HORIZONTAL_ALIGNMENT_CENTER, size.x, font_size, color)

# ═════════════════════ 贪吃蛇 ═════════════════════

const S_COLS := 24
const S_ROWS := 14
const S_CELL := 12.0
var s_snake: Array[Vector2i] = []
var s_dir: Vector2i = Vector2i(1, 0)
var s_pending_dir: Vector2i = Vector2i(1, 0)
var s_food: Vector2i = Vector2i(10, 7)
var s_timer: float = 0.0
var s_step: float = 0.14

func _board_w() -> float:
	return S_COLS * S_CELL if mode == "snake" else 320.0

func _board_h() -> float:
	return S_ROWS * S_CELL if mode == "snake" else 180.0

func _init_snake() -> void:
	s_snake = [Vector2i(6, 7), Vector2i(5, 7), Vector2i(4, 7)]
	s_food = _rand_free_cell()

func _rand_free_cell() -> Vector2i:
	var c := Vector2i(randi() % S_COLS, randi() % S_ROWS)
	while s_snake.has(c):
		c = Vector2i(randi() % S_COLS, randi() % S_ROWS)
	return c

func _key_snake(code: int) -> void:
	var d := s_dir
	if code == KEY_UP or code == KEY_W: d = Vector2i(0, -1)
	elif code == KEY_DOWN or code == KEY_S: d = Vector2i(0, 1)
	elif code == KEY_LEFT or code == KEY_A: d = Vector2i(-1, 0)
	elif code == KEY_RIGHT or code == KEY_D: d = Vector2i(1, 0)
	if d != -s_dir:
		s_pending_dir = d

func _tick_snake(delta: float) -> void:
	s_timer += delta
	if s_timer < s_step:
		return
	s_timer = 0.0
	s_dir = s_pending_dir
	var head: Vector2i = s_snake[0] + s_dir
	if head.x < 0 or head.y < 0 or head.x >= S_COLS or head.y >= S_ROWS or s_snake.has(head):
		_end(false)
		return
	s_snake.push_front(head)
	if head == s_food:
		_award(10)
		s_step = max(0.06, s_step - 0.002)  # 难度递增：越吃越快
		s_food = _rand_free_cell()
	else:
		s_snake.pop_back()

func _draw_snake() -> void:
	var o := _board_origin()
	draw_rect(Rect2(o, Vector2(_board_w(), _board_h())), COL_BG_DIM)
	_rect(s_food, S_CELL, o, COL_SCORE_COIN)
	for i in s_snake.size():
		_rect(s_snake[i], S_CELL - 1.0, o, COL_ACCENT if i == 0 else Color(0.55, 0.75, 0.9))

# ═════════════════════ Pong ═════════════════════

const P_TARGET := 7
const P_PAD_H := 44.0
const P_PAD_W := 6.0
const P_BALL_R := 4.0
var p_me: float = 90.0      # 我方挡板 y（中心）
var p_ai: float = 90.0
var p_ball: Vector2 = Vector2(160, 90)
var p_ball_v: Vector2 = Vector2(130, 70)
var p_my_score: int = 0
var p_ai_score: int = 0

func _init_pong() -> void:
	pass

func _field() -> Rect2:
	var vp := get_viewport_rect().size
	return Rect2(8, 8, vp.x - 16, vp.y - 16)

func _tick_pong(delta: float) -> void:
	var f := _field()
	# 我方挡板：方向键 / W S
	var dir := Input.get_axis("ui_up", "ui_down")
	p_me = clamp(p_me + dir * 220.0 * delta, f.position.y + P_PAD_H / 2, f.end.y - P_PAD_H / 2)
	# AI 挡板：限速跟踪
	var ai_target := p_ball.y
	p_ai = move_toward(p_ai, ai_target, 150.0 * delta)
	p_ai = clamp(p_ai, f.position.y + P_PAD_H / 2, f.end.y - P_PAD_H / 2)
	# 球
	p_ball += p_ball_v * delta
	if p_ball.y - P_BALL_R < f.position.y or p_ball.y + P_BALL_R > f.end.y:
		p_ball_v.y = -p_ball_v.y
		p_ball.y = clamp(p_ball.y, f.position.y + P_BALL_R, f.end.y - P_BALL_R)
	# 挡板碰撞（左我右 AI）
	if p_ball_v.x < 0 and p_ball.x - P_BALL_R < f.position.x + P_PAD_W + 4 \
			and p_ball.x > f.position.x and abs(p_ball.y - p_me) < P_PAD_H / 2 + P_BALL_R:
		p_ball_v.x = -p_ball_v.x * 1.03
		p_ball_v.y += (p_ball.y - p_me) * 4.0
	if p_ball_v.x > 0 and p_ball.x + P_BALL_R > f.end.x - P_PAD_W - 4 \
			and p_ball.x < f.end.x and abs(p_ball.y - p_ai) < P_PAD_H / 2 + P_BALL_R:
		p_ball_v.x = -p_ball_v.x * 1.03
		p_ball_v.y += (p_ball.y - p_ai) * 4.0
	# 得分
	if p_ball.x < f.position.x:
		p_ai_score += 1
		_reset_ball()
		if p_ai_score >= P_TARGET: _end(false)
	elif p_ball.x > f.end.x:
		p_my_score += 1
		_award(25)
		_reset_ball()
		if p_my_score >= P_TARGET: _end(true)

func _reset_ball() -> void:
	var f := _field()
	p_ball = f.size / 2.0 + f.position
	p_ball_v = Vector2(130 if randf() > 0.5 else -130, randf_range(-80, 80))

func _key_pong(_code: int) -> void:
	pass  # 移动走 Input 轴，这里留空

func _draw_pong() -> void:
	var f := _field()
	draw_rect(f, COL_BG_DIM)
	draw_rect(Rect2(f.position.x + 4, p_me - P_PAD_H / 2, P_PAD_W, P_PAD_H), COL_ACCENT)
	draw_rect(Rect2(f.end.x - 4 - P_PAD_W, p_ai - P_PAD_H / 2, P_PAD_W, P_PAD_H), COL_DANGER)
	draw_circle(p_ball, P_BALL_R, COL_FG)
	_label_center(f.position + Vector2(0, 6), Vector2(f.size.x / 2, 24), str(p_my_score), 18, COL_FG)
	_label_center(f.position + Vector2(f.size.x / 2, 6), Vector2(f.size.x / 2, 24), str(p_ai_score), 18, COL_FG)

# ═════════════════════ 2048 ═════════════════════

const M_N := 4
const M_CELL := 40.0
var m_grid: Array = []   # Array[Array[int]]

func _init_2048() -> void:
	m_grid = []
	for r in M_N:
		m_grid.append([0, 0, 0, 0])
	_spawn_2048()
	_spawn_2048()

func _spawn_2048() -> void:
	var free: Array[Vector2i] = []
	for r in M_N:
		for c in M_N:
			if m_grid[r][c] == 0:
				free.append(Vector2i(c, r))
	if free.is_empty():
		return
	var pick: Vector2i = free[randi() % free.size()]
	m_grid[pick.y][pick.x] = 2 if randf() < 0.9 else 4

func _slide_row_left(row: Array) -> Array:
	var vals := []
	for v in row:
		if v != 0:
			vals.append(v)
	var out := []
	var i := 0
	while i < vals.size():
		if i + 1 < vals.size() and vals[i] == vals[i + 1]:
			out.append(vals[i] * 2)
			_award(vals[i] * 2)
			i += 2
		else:
			out.append(vals[i])
			i += 1
	while out.size() < M_N:
		out.append(0)
	return out

func _move_2048(dir: Vector2i) -> void:
	var moved := false
	for i in M_N:
		var line := []
		for j in M_N:
			if dir == Vector2i(0, -1) or dir == Vector2i(0, 1):
				# 列方向：取第 i 列（上=正序，下=逆序）
				line.append(m_grid[j if dir == Vector2i(0, -1) else M_N - 1 - j][i])
			else:
				# 行方向：取第 i 行（左=正序，右=逆序）
				line.append(m_grid[i][j if dir == Vector2i(-1, 0) else M_N - 1 - j])
		var before := str(line)
		var slid := _slide_row_left(line)
		if str(slid) != before:
			moved = true
		for j in M_N:
			if dir == Vector2i(0, -1) or dir == Vector2i(0, 1):
				m_grid[j if dir == Vector2i(0, -1) else M_N - 1 - j][i] = slid[j]
			else:
				m_grid[i][j if dir == Vector2i(-1, 0) else M_N - 1 - j] = slid[j]
	if moved:
		_spawn_2048()
		if _has_2048():
			_end(true)
		elif not _can_move():
			_end(false)

func _has_2048() -> bool:
	for r in M_N:
		for c in M_N:
			if m_grid[r][c] >= 2048:
				return true
	return false

func _can_move() -> bool:
	for r in M_N:
		for c in M_N:
			if m_grid[r][c] == 0:
				return true
			if c + 1 < M_N and m_grid[r][c] == m_grid[r][c + 1]:
				return true
			if r + 1 < M_N and m_grid[r][c] == m_grid[r + 1][c]:
				return true
	return false

func _key_2048(code: int) -> void:
	if code == KEY_UP or code == KEY_W: _move_2048(Vector2i(0, -1))
	elif code == KEY_DOWN or code == KEY_S: _move_2048(Vector2i(0, 1))
	elif code == KEY_LEFT or code == KEY_A: _move_2048(Vector2i(-1, 0))
	elif code == KEY_RIGHT or code == KEY_D: _move_2048(Vector2i(1, 0))

func _draw_2048() -> void:
	var o := _board_origin()
	var board_px := M_N * M_CELL
	draw_rect(Rect2(o, Vector2(board_px, board_px)), COL_BG_DIM)
	var colors := {2: Color(0.85, 0.82, 0.75), 4: Color(0.85, 0.75, 0.6),
		8: Color(0.9, 0.65, 0.4), 16: Color(0.9, 0.55, 0.35), 32: Color(0.92, 0.45, 0.3),
		64: Color(0.95, 0.35, 0.25), 128: Color(0.9, 0.8, 0.3), 256: Color(0.9, 0.78, 0.2),
		512: Color(0.9, 0.75, 0.1), 1024: Color(0.85, 0.7, 0.05), 2048: Color(1.0, 0.85, 0.0)}
	for r in M_N:
		for c in M_N:
			var v: int = m_grid[r][c]
			var cell_origin := o + Vector2(c * M_CELL + 2, r * M_CELL + 2)
			draw_rect(Rect2(cell_origin, Vector2(M_CELL - 4, M_CELL - 4)),
				colors.get(v, Color(0.3, 0.3, 0.35)) if v > 0 else Color(0.15, 0.17, 0.22))
			if v > 0:
				var font := ThemeDB.fallback_font
				var text := str(v)
				var fs := 16 if v < 1024 else 13
				draw_string(font, cell_origin + Vector2(0, M_CELL / 2 + fs / 2.0), text,
					HORIZONTAL_ALIGNMENT_CENTER, M_CELL - 4, fs, Color(0.1, 0.1, 0.12))

# ═════════════════════ 打砖块 ═════════════════════

const B_COLS := 8
const B_ROWS := 5
const B_CELL := 30.0
const B_CELL_H := 12.0
var b_bricks: Array[Vector2i] = []
var b_paddle_x: float = 160.0
var b_ball: Vector2 = Vector2(160, 120)
var b_ball_v: Vector2 = Vector2(110, -140)
var b_lives: int = 3

func _init_breakout() -> void:
	for r in B_ROWS:
		for c in B_COLS:
			b_bricks.append(Vector2i(c, r))

func _bricks_left() -> int:
	return b_bricks.size()

func _tick_breakout(delta: float) -> void:
	var vp := get_viewport_rect().size
	var dir := Input.get_axis("ui_left", "ui_right")
	b_paddle_x = clamp(b_paddle_x + dir * 260.0 * delta, 30.0, vp.x - 30.0)
	b_ball += b_ball_v * delta
	if b_ball.x < 6 or b_ball.x > vp.x - 6:
		b_ball_v.x = -b_ball_v.x
		b_ball.x = clamp(b_ball.x, 6, vp.x - 6)
	if b_ball.y < 6:
		b_ball_v.y = -b_ball_v.y
		b_ball.y = 6
	# 挡板
	var paddle_y := vp.y - 14.0
	if b_ball_v.y > 0 and b_ball.y + P_BALL_R > paddle_y and b_ball.y < paddle_y + 8 \
			and abs(b_ball.x - b_paddle_x) < 32:
		b_ball_v.y = -abs(b_ball_v.y)
		b_ball_v.x += (b_ball.x - b_paddle_x) * 3.0
	# 砖块碰撞（简单网格映射）
	var origin := _breakout_origin()
	var gx := int((b_ball.x - origin.x) / B_CELL)
	var gy := int((b_ball.y - origin.y) / B_CELL_H)
	var cell := Vector2i(gx, gy)
	if b_bricks.has(cell):
		b_bricks.erase(cell)
		b_ball_v.y = -b_ball_v.y
		_award(15)
		if _bricks_left() == 0:
			_end(true)
			return
	# 掉落
	if b_ball.y > vp.y + 10:
		b_lives -= 1
		if b_lives <= 0:
			_end(false)
		else:
			b_ball = Vector2(vp.x / 2, vp.y / 2)
			b_ball_v = Vector2(110, -140)

func _breakout_origin() -> Vector2:
	var vp := get_viewport_rect().size
	return Vector2((vp.x - B_COLS * B_CELL) / 2.0, 24)

func _key_breakout(code: int) -> void:
	if code == KEY_R and b_lives < 3:
		pass  # 保留：无中途重开，走 game_flow

func _draw_breakout() -> void:
	var vp := get_viewport_rect().size
	var origin := _breakout_origin()
	var row_colors := [Color(0.95, 0.5, 0.5), Color(0.95, 0.75, 0.4), Color(0.95, 0.92, 0.5),
		Color(0.5, 0.9, 0.55), Color(0.5, 0.7, 0.95)]
	for brick in b_bricks:
		draw_rect(Rect2(origin + Vector2(brick.x * B_CELL + 1, brick.y * B_CELL_H + 1),
			Vector2(B_CELL - 2, B_CELL_H - 2)), row_colors[brick.y % row_colors.size()])
	draw_rect(Rect2(Vector2(b_paddle_x - 32, vp.y - 14), Vector2(64, 8)), COL_ACCENT)
	draw_circle(b_ball, P_BALL_R, COL_FG)
	_label_center(Vector2(vp.x - 90, 4), Vector2(86, 18), "生命 x%d" % b_lives, 13, COL_FG)

# ═════════════════════ 推箱子 ═════════════════════

const K_MAP := [
	"##########",
	"#........#",
	"#.o.$....#",
	"#...@....#",
	"#.o.$....#",
	"#........#",
	"##########",
]
var k_state: Array[String] = []
var k_player: Vector2i = Vector2i(4, 3)

func _init_sokoban() -> void:
	_reset_sokoban()

func _reset_sokoban() -> void:
	k_state.clear()
	for line in K_MAP:
		k_state.append(line)
	_locate_player()

func _locate_player() -> void:
	for y in k_state.size():
		var x := k_state[y].find("@")
		if x >= 0:
			k_player = Vector2i(x, y)
			return

func _tile(pos: Vector2i) -> String:
	if pos.y < 0 or pos.y >= k_state.size() or pos.x < 0 or pos.x >= k_state[pos.y].length():
		return "#"
	return k_state[pos.y][pos.x]

func _set_tile(pos: Vector2i, ch: String) -> void:
	k_state[pos.y] = k_state[pos.y].substr(0, pos.x) + ch + k_state[pos.y].substr(pos.x + 1)

func _try_push(dir: Vector2i) -> void:
	var target := k_player + dir
	var t := _tile(target)
	if t == "#":
		return
	if t == "$" or t == "*":
		var beyond := target + dir
		var b := _tile(beyond)
		if b == "#" or b == "$" or b == "*":
			return
		# 推箱子（目标点上为 * ，空地为 $）
		_set_tile(beyond, "*" if b == "o" else "$")
		_set_tile(target, "@" if t == "$" else "o")
	if t == "." or t == "o" or t == "$" or t == "*":
		_set_tile(k_player, "." if _tile(k_player) == "@" else "o")
		k_player = target
		_set_tile(k_player, "@")
	_check_sokoban_win()

func _check_sokoban_win() -> void:
	# 所有 $ 都变成 *（箱子在目标点）即胜利
	for y in k_state.size():
		if k_state[y].contains("$"):
			return
	_award(100)
	_end(true)

func _key_sokoban(code: int) -> void:
	var d := Vector2i.ZERO
	if code == KEY_UP or code == KEY_W: d = Vector2i(0, -1)
	elif code == KEY_DOWN or code == KEY_S: d = Vector2i(0, 1)
	elif code == KEY_LEFT or code == KEY_A: d = Vector2i(-1, 0)
	elif code == KEY_RIGHT or code == KEY_D: d = Vector2i(1, 0)
	elif code == KEY_R:
		_reset_sokoban()
		return
	if d != Vector2i.ZERO:
		_try_push(d)

func _draw_sokoban() -> void:
	var o := _board_origin()
	var cs := 16.0
	var board := Vector2(k_state[0].length() * cs, k_state.size() * cs)
	draw_rect(Rect2(o - Vector2(4, 4), board + Vector2(8, 8)), COL_BG_DIM)
	for y in k_state.size():
		for x in k_state[y].length():
			var ch := k_state[y][x]
			var cell := o + Vector2(x * cs, y * cs)
			match ch:
				"#": draw_rect(Rect2(cell, Vector2(cs, cs)), Color(0.35, 0.38, 0.45))
				"o": draw_rect(Rect2(cell + Vector2(4, 4), Vector2(cs - 8, cs - 8)), Color(0.3, 0.7, 0.4))
				"$": draw_rect(Rect2(cell + Vector2(2, 2), Vector2(cs - 4, cs - 4)), Color(0.85, 0.65, 0.3))
				"*": draw_rect(Rect2(cell + Vector2(2, 2), Vector2(cs - 4, cs - 4)), Color(0.4, 0.9, 0.4))
				"@": draw_rect(Rect2(cell + Vector2(2, 2), Vector2(cs - 4, cs - 4)), COL_ACCENT)

# ═════════════════════ 扫雷 ═════════════════════

const MS_COLS := 14
const MS_ROWS := 10
const MS_MINES := 18
const MS_CELL := 16.0
var ms_mines: Array[Vector2i] = []
var ms_revealed: Array[Vector2i] = []
var ms_flags: Array[Vector2i] = []
var ms_started: bool = false

func _init_mines() -> void:
	pass

func _place_mines(safe: Vector2i) -> void:
	ms_mines.clear()
	while ms_mines.size() < MS_MINES:
		var c := Vector2i(randi() % MS_COLS, randi() % MS_ROWS)
		if not ms_mines.has(c) and abs(c.x - safe.x) + abs(c.y - safe.y) > 2:
			ms_mines.append(c)
	ms_started = true

func _neighbors(c: Vector2i) -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	for dy in range(-1, 2):
		for dx in range(-1, 2):
			if dx == 0 and dy == 0:
				continue
			var n := c + Vector2i(dx, dy)
			if n.x >= 0 and n.y >= 0 and n.x < MS_COLS and n.y < MS_ROWS:
				out.append(n)
	return out

func _count_mines(c: Vector2i) -> int:
	var n := 0
	for nb in _neighbors(c):
		if ms_mines.has(nb):
			n += 1
	return n

func _reveal(c: Vector2i) -> void:
	if ms_revealed.has(c) or ms_flags.has(c):
		return
	ms_revealed.append(c)
	if _count_mines(c) == 0 and not ms_mines.has(c):
		for nb in _neighbors(c):
			_reveal(nb)

func _click_mines(mb: InputEventMouseButton) -> void:
	var o := _board_origin()
	var local := (mb.position - o) / MS_CELL
	var cell := Vector2i(int(local.x), int(local.y))
	if cell.x < 0 or cell.y < 0 or cell.x >= MS_COLS or cell.y >= MS_ROWS:
		return
	if not ms_started:
		_place_mines(cell)
	if mb.button_index == MOUSE_BUTTON_RIGHT:
		if ms_flags.has(cell):
			ms_flags.erase(cell)
		elif not ms_revealed.has(cell):
			ms_flags.append(cell)
		return
	if ms_mines.has(cell):
		_end(false)
		return
	_reveal(cell)
	_award(5)
	# 翻开所有非雷格 → 胜利
	if ms_revealed.size() >= MS_COLS * MS_ROWS - MS_MINES:
		_award(100)
		_end(true)

func _key_mines(code: int) -> void:
	if code == KEY_R:
		ms_mines.clear()
		ms_revealed.clear()
		ms_flags.clear()
		ms_started = false

func _draw_mines() -> void:
	var o := _board_origin()
	draw_rect(Rect2(o, Vector2(MS_COLS * MS_CELL, MS_ROWS * MS_CELL)), COL_BG_DIM)
	for y in MS_ROWS:
		for x in MS_COLS:
			var c := Vector2i(x, y)
			var cell := o + Vector2(x * MS_CELL, y * MS_CELL)
			var revealed := ms_revealed.has(c)
			draw_rect(Rect2(cell + Vector2(1, 1), Vector2(MS_CELL - 2, MS_CELL - 2)),
				Color(0.2, 0.23, 0.3) if revealed else Color(0.45, 0.5, 0.6))
			if revealed and not ms_mines.has(c):
				var n := _count_mines(c)
				if n > 0:
					var font := ThemeDB.fallback_font
					draw_string(font, cell + Vector2(0, MS_CELL - 4), str(n),
						HORIZONTAL_ALIGNMENT_CENTER, MS_CELL - 2, 11, COL_ACCENT)
			if ms_flags.has(c):
				_label_center(cell, Vector2(MS_CELL, MS_CELL), "F", 11, COL_SCORE_COIN)
			if ms_mines.has(c) and _finished:
				draw_circle(cell + Vector2(MS_CELL / 2, MS_CELL / 2), 4, COL_DANGER)
''',
}


# ═══════════════════════════════════════════════════════════════
# 写入工程
# ═══════════════════════════════════════════════════════════════


def write_project(
    project_path: str,
    scene_ir: SceneIR,
    *,
    width: int = 640,
    height: int = 360,
    assets: Optional[Dict[str, str]] = None,
    layout_seed: int = 0,
) -> Dict[str, Any]:
    """把 SceneIR 完整写入一个 Godot 项目。

    assets: 可选 AI 素材（见 asset_forge.forge_assets），缺省用色块视觉。
    返回 {"tscn_path": ..., "scene_count": ..., "entity_count": ...}
    """
    # 1. 写运行时脚本（被 .tscn 引用）
    runtime_dir = os.path.join(project_path, "addons", "gameforge", "runtime")
    os.makedirs(runtime_dir, exist_ok=True)
    for fn, code in RUNTIME_SCRIPTS.items():
        with open(os.path.join(runtime_dir, fn), "w", encoding="utf-8") as f:
            f.write(code)

    # 2. 写 .tscn（固定文件名 main.tscn，与 API 自动生成检测、_pick_scene 约定一致）
    scenes_dir = os.path.join(project_path, "scenes")
    os.makedirs(scenes_dir, exist_ok=True)
    scene_name = _sanitize_node_name(scene_ir.scene_name or "GameScene")
    tscn_text = build_scene_tscn(scene_ir, width=width, height=height, assets=assets, layout_seed=layout_seed)
    tscn_path = os.path.join(scenes_dir, "main.tscn")
    with open(tscn_path, "w", encoding="utf-8") as f:
        f.write(tscn_text)

    # 2.5 上线件：AI 音频（BGM + 音效），失败自动回退程序化 8-bit + 导出预设
    try:
        from src.engine.godot.audio_engine import generate_audio_for_project

        genre = getattr(scene_ir, "genre", None) or "platformer"
        generate_audio_for_project(project_path, genre=genre)
    except Exception as e:  # noqa: BLE001
        logger.warning("write_project.audio_failed", error=str(e))
        try:
            from src.engine.godot.sfx_forge import write_sfx

            write_sfx(project_path)
        except Exception as e2:  # noqa: BLE001
            logger.warning("write_project.sfx_fallback_failed", error=str(e2))
    try:
        from src.engine.godot.export_kit import write_export_presets

        write_export_presets(project_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("write_project.presets_failed", error=str(e))
    # 游戏图标：AI 素材管线产出了 icon 时提升到项目根（project.godot 引用 res://icon.png）
    gen_icon = os.path.join(project_path, "assets", "gen", "icon.png")
    root_icon = os.path.join(project_path, "icon.png")
    if os.path.isfile(gen_icon) and not os.path.isfile(root_icon):
        try:
            import shutil

            shutil.copyfile(gen_icon, root_icon)
        except Exception as e:  # noqa: BLE001
            logger.warning("write_project.icon_copy_failed", error=str(e))
    if not os.path.isfile(root_icon):
        # 兜底图标：纯色像素风方块，保证 project.godot 引用不落空
        try:
            from PIL import Image

            img = Image.new("RGBA", (128, 128), (46, 26, 90, 255))
            for x in range(24, 104):
                for y in range(24, 104):
                    if (x // 8 + y // 8) % 2 == 0:
                        img.putpixel((x, y), (240, 200, 80, 255))
            img.save(root_icon)
        except Exception as e:  # noqa: BLE001
            logger.warning("write_project.icon_fallback_failed", error=str(e))

    # 3. 写 project.godot（如不存在）
    project_godot = os.path.join(project_path, "project.godot")
    if not os.path.isfile(project_godot):
        pg_text = _minimal_project_godot(scene_name, width=width, height=height)
        with open(project_godot, "w", encoding="utf-8") as f:
            f.write(pg_text)

    return {
        "tscn_path": tscn_path,
        "scene_name": scene_name,
        "entity_count": sum(1 + max(0, e.count - 1) for e in scene_ir.entities),
        "runtime_scripts": list(RUNTIME_SCRIPTS.keys()),
    }


def _minimal_project_godot(scene_name: str, *, width: int = 640, height: int = 360) -> str:
    return f'''config_version=5

[application]

config/name="GameForge Preview {scene_name}"
config/description="GameForge AI generated — {scene_name}"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("4.6", "GL Compatibility")
config/icon="res://icon.png"

[display]
window/size/viewport_width={width}
window/size/viewport_height={height}

[rendering]
renderer/rendering_method="gl_compatibility"
renderer/rendering_method.mobile="gl_compatibility"
'''


# ═══════════════════════════════════════════════════════════════
# SceneIR 简易生成（无需 LLM，先演示用）
# ═══════════════════════════════════════════════════════════════


def default_scene_ir(
    theme: str = "sky_blue",
    genre: str = "platformer",
    difficulty: Optional[str] = None,
) -> SceneIR:
    """品类感知 + 难度分级的默认场景 IR。

    有品类规格时按规格库蓝图生成实体（代表基款的经典构成）；
    未知品类回退 platformer 经典蓝图（与 SceneIR 校验回退一致）。
    difficulty（easy/medium/hard）缩放实体数量：简易版成品精简、高难度加量。
    """
    from src.agents.genre_specs import get_spec, infer_difficulty, scale_count
    from src.agents.scene_ir import CameraIR, EntityIR

    spec = get_spec(genre)
    difficulty = difficulty or "medium"
    entity_scale = {"easy": 0.6, "medium": 1.0, "hard": 1.4}.get(difficulty, 1.0)
    role_script = {
        "player": "PlayerController",
        "enemy": "EnemyController",
        "pickup": "CoinController",
    }
    # 同角色多实体时名字必须唯一（.tscn 重名节点会导致场景解析失败）
    seen: Dict[str, int] = {}
    entities = []
    for e in spec.entities:
        count = scale_count(int(e.get("count", "1")), entity_scale)
        base = e["name"]
        for i in range(count):
            name = base if count == 1 else f"{base}{i + 1}"
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                name = f"{name}_{seen[name]}"
            entities.append(EntityIR(
                name=name,
                role=e["role"],
                count=1,
                spawn_zone=e.get("spawn_zone", "center"),
                script=role_script.get(e["role"]),
            ))

    return SceneIR(
        scene_name="GameScene",
        genre=spec.id,
        layout="linear" if spec.camera == "2d_side_view" else "arena",
        difficulty=difficulty,
        theme=theme or spec.theme,
        camera=CameraIR(mode=spec.camera, follow_target="Player", background=theme or spec.theme),
        entities=entities,
    )