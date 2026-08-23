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
import textwrap
from typing import Any, Dict, List, Optional

from src.agents.scene_ir import SceneIR, EntityIR


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


def _ext_resource(idx: int, path: str, type_: str = "Script") -> str:
    """生成 [ext_resource ...] 行"""
    return f'[ext_resource type="{type_}" path="{path}" id="{idx}_{type_.lower()}"]'


def build_scene_tscn(scene_ir: SceneIR, width: int = 640, height: int = 360) -> str:
    """生成 Godot 4 兼容的 .tscn 文本。"""
    palette = _theme_for(scene_ir.theme, scene_ir.camera.background if scene_ir.camera else None)

    # 实体分类（按 role 决定使用什么渲染策略）
    entities: List[EntityIR] = scene_ir.entities
    platforms = [e for e in entities if e.role in ("ground", "platform")]
    players   = [e for e in entities if e.role == "player"]
    enemies   = [e for e in entities if e.role == "enemy"]
    npcs      = [e for e in entities if e.role == "npc"]
    pickups   = [e for e in entities if e.role == "pickup"]
    decorations = [e for e in entities if e.role == "decoration"]
    boundary  = [e for e in entities if e.role == "boundary"]

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

    # ---- 节点构建 ----
    scene_name = scene_ir.scene_name or "GameScene"
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
            x, y, w, h = -200 + idx * 120, 100 - idx * 30, 100, 16
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

    # 3. 主角（CharacterBody2D + 碰撞形状 + 弹跳视觉）
    _sub_rect("shape_player", 32, 48)
    for _idx, p in enumerate(players):
        nodes.append(_node_header("Player", "CharacterBody2D", parent="."))
        nodes.append(f"position = Vector2({-width // 2 + 100}, {height // 2 - 80})")
        nodes.append("script = ExtResource(\"" + ext_player + "\")")
        nodes.append("speed = 120.0")
        nodes.append("jump_velocity = -260.0")
        nodes.append("gravity = 600.0")
        nodes.append(_node_header("CollisionShape2D", "CollisionShape2D", parent="Player"))
        nodes.append('shape = SubResource("shape_player")')
        nodes.append(_node_header("PlayerVisual", "ColorRect", parent="Player"))
        nodes.append("color = " + _rgba(palette["accent"], 1.0))
        nodes.append("offset_left = -16")
        nodes.append("offset_top = -24")
        nodes.append("offset_right = 16")
        nodes.append("offset_bottom = 24")
        nodes.append("script = ExtResource(\"" + ext_bouncer + "\")")
        nodes.append("bounce_height = 6.0")
        nodes.append("bounce_speed = 2.5")
        nodes.append("mouse_filter = 2")

    # 4. 敌人（巡逻）
    for idx, e in enumerate(enemies):
        nm = f"Enemy{idx+1}"
        nodes.append(_node_header(nm, "CharacterBody2D", parent="."))
        nodes.append(f"position = Vector2({width // 2 - 80 + idx * 60}, {height // 2 - 60})")
        nodes.append("script = ExtResource(\"" + ext_walker + "\")")
        nodes.append("speed = 50.0")
        nodes.append("range = 80.0")
        nodes.append("color_seed = " + str(idx * 31))
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + _rgba([220, 80, 80], 1.0))
        nodes.append("offset_left = -14")
        nodes.append("offset_top = -14")
        nodes.append("offset_right = 14")
        nodes.append("offset_bottom = 14")
        nodes.append("mouse_filter = 2")

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

    # 6. 道具（漂浮金币，玩家碰到收集）
    for idx, pk in enumerate(pickups):
        nm = f"Pickup{idx+1}"
        nodes.append(_node_header(nm, "Area2D", parent="."))
        nodes.append(f"position = Vector2({-200 + idx * 80}, {60 + (idx % 2) * 40})")
        nodes.append("script = ExtResource(\"" + ext_pickup + "\")")
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + _rgba([255, 220, 60], 1.0))
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
        connections.append(
            f'[connection signal="body_entered" from="{nm}" to="{nm}" method="_on_body_entered"]'
        )

    # 7. 装饰物（树/石头，立在地面附近）
    for idx, d in enumerate(decorations):
        nm = f"Prop{idx+1}"
        nodes.append(_node_header(nm, "Node2D", parent="."))
        nodes.append(f"position = Vector2({-width // 2 + 60 + idx * 90}, {height // 2 - 40 - (idx % 3) * 24})")
        nodes.append(_node_header("Visual", "ColorRect", parent=nm))
        nodes.append("color = " + _rgba(palette["ground"], 0.9))
        nodes.append("offset_left = -8")
        nodes.append("offset_top = -16")
        nodes.append("offset_right = 8")
        nodes.append("offset_bottom = 16")
        nodes.append("mouse_filter = 2")

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
## 道具：自动漂浮 + 旋转 + 玩家碰到后消失
@export var collected: bool = false

func _on_body_entered(body: Node) -> void:
    if collected: return
    if body.name == "Player" or body.is_in_group("player"):
        collected = true
        var tween := create_tween()
        tween.set_parallel(true)
        tween.tween_property(self, "scale", Vector2(1.8, 1.8), 0.15)
        tween.tween_property(self, "modulate:a", 0.0, 0.2)
        tween.chain().tween_callback(queue_free)
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

    "hud.gd": '''extends Control
## HUD：计时 + 分数（用玩家位置做伪分）。挂在 HUD/HudRoot 下，Label 兄弟节点为 HUD 直接子。
@export var theme_color: Color = Color.WHITE

var _t0: int = 0
var _score: int = 0

func _ready() -> void:
    _t0 = Time.get_ticks_msec()
    set_process(true)

func _process(_delta: float) -> void:
    var elapsed: int = Time.get_ticks_msec() - _t0
    var sec: int = int(elapsed / 1000)
    var mm: String = "%02d" % int(sec / 60)
    var ss: String = "%02d" % int(sec % 60)
    var hud: Node = get_parent()
    var tl: Label = hud.get_node_or_null("TimeLabel") as Label
    if tl:
        tl.text = mm + ":" + ss
    var sl: Label = hud.get_node_or_null("ScoreLabel") as Label
    if sl:
        sl.text = "SCORE " + str(_score + int(elapsed / 1000) * 7)
''',

    "player.gd": '''extends CharacterBody2D
## 玩家：受重力、自动移动 + 跳跃（演示用）
@export var speed: float = 120.0
@export var jump_velocity: float = -260.0
@export var gravity: float = 600.0

var _t: float = 0.0

func _physics_process(delta: float) -> void:
    _t += delta
    # 自动前进 + 周期性跳跃（演示用，无输入）
    if is_on_floor():
        velocity.y = jump_velocity
    velocity.x = speed
    velocity.y += gravity * delta
    move_and_slide()
    # 到达右边界就 wrap 回左
    if position.x > 320.0:
        position.x = -320.0
''',
}


# ═══════════════════════════════════════════════════════════════
# 写入工程
# ═══════════════════════════════════════════════════════════════


def write_project(project_path: str, scene_ir: SceneIR, *, width: int = 640, height: int = 360) -> Dict[str, Any]:
    """把 SceneIR 完整写入一个 Godot 项目。

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
    scene_name = scene_ir.scene_name or "GameScene"
    tscn_text = build_scene_tscn(scene_ir, width=width, height=height)
    tscn_path = os.path.join(scenes_dir, "main.tscn")
    with open(tscn_path, "w", encoding="utf-8") as f:
        f.write(tscn_text)

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


def default_scene_ir(theme: str = "sky_blue", genre: str = "platformer") -> SceneIR:
    """默认丰富的场景 IR"""
    from src.agents.scene_ir import CameraIR, EntityIR
    return SceneIR(
        scene_name="GameScene",
        genre=genre,
        layout="linear",
        difficulty="easy",
        theme=theme,
        camera=CameraIR(mode="2d_side_view", follow_target="Player", background=theme),
        entities=[
            EntityIR(name="Player", role="player", count=1, spawn_zone="left", script="PlayerController"),
            EntityIR(name="Ground", role="ground", count=1, spawn_zone="bottom", script=None),
            EntityIR(name="Platform1", role="platform", count=1, spawn_zone="center", script=None),
            EntityIR(name="Platform2", role="platform", count=1, spawn_zone="right", script=None),
            EntityIR(name="Coin1", role="pickup", count=1, spawn_zone="center", script="CoinController"),
            EntityIR(name="Coin2", role="pickup", count=1, spawn_zone="right", script="CoinController"),
            EntityIR(name="Enemy1", role="enemy", count=1, spawn_zone="right", script="EnemyController"),
            EntityIR(name="Enemy2", role="enemy", count=1, spawn_zone="center", script="EnemyController"),
            EntityIR(name="NPC", role="npc", count=1, spawn_zone="center", script=None),
            EntityIR(name="Tree1", role="decoration", count=1, spawn_zone="left", script=None),
            EntityIR(name="Tree2", role="decoration", count=1, spawn_zone="right", script=None),
        ],
    )