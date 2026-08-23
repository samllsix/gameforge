"""GameForge - Godot 常用组件参数化模板库（P0 提速快路径）

把 code_generator 里原先散落的"死模板"（硬编码 if-elif、参数写死、仅 LLM 失败兜底）
统一为带占位符的参数化模板：
- 命中某已知标准组件（Player/Enemy/Coin/Camera/UI/GameManager…）时直接填参返回，
  跳过一次全量 LLM 生成；
- 参数从 GDM / task 提取，提取不到回退到已验证的默认值，组件职责与可运行性不变。

所有模板均为 Godot 4 可运行骨架（CharacterBody2D / Area2D / Node / CanvasLayer / Camera2D），
与 code_generator 原有的 `_generate_*_godot` 兜底模板内容一致，仅将硬编码数值参数化。
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 模板注册表
# ---------------------------------------------------------------------------

def _player():
    return '''## 玩家控制器 — 移动、跳跃
extends CharacterBody2D

# ==================== 信号 ====================
signal health_changed(new_health: int)
signal died()

# ==================== 导出变量 ====================
@export var move_speed: float = {{move_speed}}
@export var jump_velocity: float = {{jump_velocity}}

# ==================== 私有变量 ====================
var _health: int = {{player_health}}
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)

@onready var _sprite: AnimatedSprite2D = $AnimatedSprite2D

func _physics_process(delta: float) -> void:
    var direction := Input.get_action_strength("move_right") - Input.get_action_strength("move_left")
    velocity.x = direction * move_speed
    velocity.y += _gravity * delta
    if Input.is_action_just_pressed("jump") and is_on_floor():
        velocity.y = jump_velocity
    move_and_slide()

func take_damage(amount: int = 1) -> void:
    _health -= amount
    health_changed.emit(_health)
    if _health <= 0:
        died.emit()
'''


def _enemy():
    return '''## 敌人控制器 — 左右巡逻
extends CharacterBody2D

@export var move_speed: float = {{enemy_speed}}
@export var patrol_distance: float = {{patrol_distance}}

var _start_x: float = 0.0
var _dir: int = 1
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)

func _ready() -> void:
    _start_x = global_position.x

func _physics_process(delta: float) -> void:
    if abs(global_position.x - _start_x) >= patrol_distance:
        _dir *= -1
    velocity.x = _dir * move_speed
    velocity.y += _gravity * delta
    move_and_slide()

func _on_body_entered(body: Node) -> void:
    if body.is_in_group("player"):
        if body.has_method("take_damage"):
            body.take_damage(1)
'''


def _coin():
    return '''## 金币 — 收集 + 浮动动画
extends Area2D

@export var score_value: int = {{score_value}}

var _start_y: float = 0.0

func _ready() -> void:
    _start_y = global_position.y
    body_entered.connect(_on_body_entered)

func _process(delta: float) -> void:
    global_position.y = _start_y + sin(Time.get_ticks_msec() / 200.0) * 6.0

func _on_body_entered(body: Node) -> void:
    if body.is_in_group("player"):
        if Engine.has_singleton("GameManager") or get_tree().root.has_node("GameManager"):
            pass
        queue_free()
'''


def _camera():
    return '''## 摄像机跟随
extends Camera2D

@export var follow_target: NodePath = ^""
@export var smooth_speed: float = {{smooth_speed}}

func _physics_process(delta: float) -> void:
    var target := get_node_or_null(follow_target)
    if target:
        global_position = global_position.lerp(target.global_position, smooth_speed * delta)
'''


def _ui():
    return '''## HUD 管理器
extends CanvasLayer

@onready var _score_label: Label = $ScoreLabel
@onready var _health_label: Label = $HealthLabel

func _ready() -> void:
    var gm := get_tree().root.get_node_or_null("GameManager")
    if gm:
        if gm.has_signal("score_changed"):
            gm.score_changed.connect(_on_score_changed)
        if gm.has_signal("health_changed"):
            gm.health_changed.connect(_on_health_changed)

func _on_score_changed(new_score: int) -> void:
    if _score_label:
        _score_label.text = "Score: %d" % new_score

func _on_health_changed(new_health: int) -> void:
    if _health_label:
        _health_label.text = "HP: %d" % new_health
'''


def _game_manager():
    return '''## 游戏管理器 — 全局计分与状态（Autoload 单例）
extends Node

# ==================== 信号 ====================
signal score_changed(new_score: int)
signal health_changed(new_health: int)
signal game_over()
signal game_won()

# ==================== 私有变量 ====================
var _score: int = 0
var _health: int = {{player_health}}
var _coins: int = 0
var _total_coins: int = {{total_coins}}

func add_score(amount: int) -> void:
    _score += amount
    score_changed.emit(_score)

func collect_coin() -> void:
    _coins += 1
    add_score(1)
    if _coins >= _total_coins:
        game_won.emit()

func take_damage(amount: int = 1) -> void:
    _health -= amount
    health_changed.emit(_health)
    if _health <= 0:
        game_over.emit()
'''


def _moving_platform():
    return '''## 移动平台 — 水平往返移动（可作弹跳平台）
extends StaticBody2D

@export var travel_distance: float = {{travel_distance}}
@export var move_speed: float = {{platform_speed}}

var _start_x: float = 0.0
var _dir: int = 1

func _ready() -> void:
    _start_x = global_position.x

func _physics_process(delta: float) -> void:
    if abs(global_position.x - _start_x) >= travel_distance:
        _dir *= -1
    global_position.x += _dir * move_speed * delta
'''


def _bullet():
    return '''## 子弹/投射物 — 沿方向直线飞行并造成伤害
extends Area2D

@export var bullet_speed: float = {{bullet_speed}}
@export var damage: int = {{bullet_damage}}

var _dir: Vector2 = Vector2.RIGHT

func _ready() -> void:
    body_entered.connect(_on_body_entered)

func _physics_process(delta: float) -> void:
    global_position += _dir * bullet_speed * delta

func shoot(dir: Vector2) -> void:
    _dir = dir.normalized()

func _on_body_entered(body: Node) -> void:
    if body.has_method("take_damage"):
        body.take_damage(damage)
    queue_free()
'''


def _hazard():
    return '''## 陷阱/尖刺 — 接触造成伤害的区域
extends Area2D

@export var damage: int = {{hazard_damage}}

func _ready() -> void:
    body_entered.connect(_on_body_entered)

func _on_body_entered(body: Node) -> void:
    if body.is_in_group("player"):
        if body.has_method("take_damage"):
            body.take_damage(damage)
'''


def _ground():
    return '''## 地面/平台 — 静态碰撞体
extends StaticBody2D

# 在场景中为这个节点添加 CollisionShape2D 即可定义碰撞形状
'''


def _audio():
    return '''## 音效管理器 — 全局音效/音乐播放（Autoload 单例）
extends Node

@export var fallback_audio: AudioStream

func play_sfx(stream: AudioStream, volume_db: float = {{audio_volume}}) -> void:
    if not stream:
        return
    var player := AudioStreamPlayer.new()
    player.stream = stream
    player.volume_db = volume_db
    add_child(player)
    player.play()
    await player.finished
    player.queue_free()
'''


def _level():
    return '''## 关卡管理器 — 管理与切换关卡
extends Node

signal level_changed(level_index: int)
signal game_won()

@export var total_levels: int = {{total_levels}}

var _current_level: int = 0

func next_level() -> void:
    _current_level += 1
    if _current_level >= total_levels:
        game_won.emit()
    else:
        level_changed.emit(_current_level)

func restart_level() -> void:
    level_changed.emit(_current_level)
'''


# 组件定义：kind -> (aliases, file_path, template_fn, param_defaults)
_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "moving_platform": {  # 靠前：别名含"平台"，避免被 ground 抢占
        "aliases": ["移动平台", "moving platform", "弹跳板", "bouncer", "弹簧", "蹦床"],
        "file_path": "res://scripts/platforms/moving_platform.gd",
        "template": _moving_platform,
        "defaults": {"travel_distance": 160.0, "platform_speed": 80.0},
    },
    "player": {
        "aliases": ["player", "玩家", "角色控制器", "主角"],
        "file_path": "res://scripts/player/player_controller.gd",
        "template": _player,
        "defaults": {"move_speed": 200.0, "jump_velocity": -400.0, "player_health": 3},
    },
    "enemy": {
        "aliases": ["enemy", "敌人", "怪物", "小怪"],
        "file_path": "res://scripts/enemy/enemy_controller.gd",
        "template": _enemy,
        "defaults": {"enemy_speed": 60.0, "patrol_distance": 120.0},
    },
    "coin": {
        "aliases": ["coin", "金币", "道具", "收集", "拾取", "collectible"],
        "file_path": "res://scripts/collectibles/coin_controller.gd",
        "template": _coin,
        "defaults": {"score_value": 1},
    },
    "camera": {
        "aliases": ["camera", "摄像机", "相机", "镜头"],
        "file_path": "res://scripts/camera/camera_follow.gd",
        "template": _camera,
        "defaults": {"smooth_speed": 5.0},
    },
    "ui": {
        "aliases": ["ui", "hud", "界面", "菜单", "hud 管理器"],
        "file_path": "res://scripts/ui/ui_manager.gd",
        "template": _ui,
        "defaults": {},
    },
    "game_manager": {
        "aliases": ["game manager", "游戏管理", "游戏管理器", "计分", "score", "game_manager"],
        "file_path": "res://scripts/game_manager.gd",
        "template": _game_manager,
        "defaults": {"player_health": 3, "total_coins": 5},
    },
    "bullet": {
        "aliases": ["bullet", "子弹", "投射物", "projectile", "弹药"],
        "file_path": "res://scripts/bullets/bullet_controller.gd",
        "template": _bullet,
        "defaults": {"bullet_speed": 600.0, "bullet_damage": 1},
    },
    "hazard": {
        "aliases": ["hazard", "陷阱", "尖刺", "spike", "障碍物", "伤害区"],
        "file_path": "res://scripts/hazards/hazard_controller.gd",
        "template": _hazard,
        "defaults": {"hazard_damage": 1},
    },
    "ground": {
        "aliases": ["ground", "地面", "地板", "地形"],
        "file_path": "res://scripts/ground/ground_controller.gd",
        "template": _ground,
        "defaults": {},
    },
    "audio": {
        "aliases": ["audio", "音效", "音乐", "声音", "audio manager"],
        "file_path": "res://scripts/audio/audio_manager.gd",
        "template": _audio,
        "defaults": {"audio_volume": -5.0},
    },
    "level": {
        "aliases": ["level", "关卡", "level manager", "关卡管理"],
        "file_path": "res://scripts/levels/level_manager.gd",
        "template": _level,
        "defaults": {"total_levels": 3},
    },
}


def match_component(task: Dict[str, Any], requirements: str = "") -> Optional[str]:
    """根据任务名/描述关键词，识别命中的标准组件 kind。

    只扫任务名与描述（精确、避免宽泛需求误命中）；
    按注册顺序返回第一个命中，避免 game_manager/ui/coin/moving_platform 相互误命中。
    """
    task_name = (task or {}).get("name", "")
    task_desc = (task or {}).get("description", "")

    for kind, tpl in _TEMPLATES.items():
        for alias in tpl["aliases"]:
            a = alias.lower()
            if a in task_name.lower():
                return kind
            if a in task_desc.lower():
                return kind
    return None


def _lookup_number(obj: Any, keys: List[str], default: Any) -> Any:
    """从 GDM 半结构化容器中按 key 链查找数值，找不到回退默认。"""
    if not isinstance(obj, dict):
        return default
    for k in keys:
        v = obj.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return default


def extract_params(kind: str, gdm: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    """为指定组件提取填充参数：GDM / task 有明确值则覆盖，否则用模板默认值。"""
    tpl = _TEMPLATES.get(kind)
    if not tpl:
        return {}
    params = dict(tpl["defaults"])

    if kind == "player":
        physics = (gdm or {}).get("physics_settings", {}) or {}
        params["move_speed"] = _lookup_number(physics, ["move_speed", "speed"], params["move_speed"])
        params["jump_velocity"] = _lookup_number(physics, ["jump_velocity", "jump"], params["jump_velocity"])
        params["player_health"] = _lookup_number(physics, ["player_health", "health"], params["player_health"])
    elif kind == "enemy":
        physics = (gdm or {}).get("physics_settings", {}) or {}
        params["enemy_speed"] = _lookup_number(physics, ["enemy_speed", "speed"], params["enemy_speed"])
        params["patrol_distance"] = _lookup_number(physics, ["patrol_distance"], params["patrol_distance"])
    elif kind == "coin":
        params["score_value"] = _lookup_number(gdm, ["score_value"], params["score_value"])
    elif kind == "camera":
        params["smooth_speed"] = _lookup_number(gdm, ["smooth_speed"], params["smooth_speed"])
    elif kind == "game_manager":
        physics = (gdm or {}).get("physics_settings", {}) or {}
        params["player_health"] = _lookup_number(physics, ["player_health", "health"], params["player_health"])
        params["total_coins"] = _lookup_number(gdm, ["total_coins"], params["total_coins"])
    elif kind == "bullet":
        params["bullet_speed"] = _lookup_number(gdm, ["bullet_speed"], params["bullet_speed"])
        params["bullet_damage"] = _lookup_number(gdm, ["bullet_damage", "damage"], params["bullet_damage"])
    elif kind == "hazard":
        params["hazard_damage"] = _lookup_number(gdm, ["hazard_damage", "damage"], params["hazard_damage"])
    elif kind == "level":
        params["total_levels"] = _lookup_number(gdm, ["total_levels"], params["total_levels"])
    elif kind == "moving_platform":
        params["travel_distance"] = _lookup_number(gdm, ["travel_distance"], params["travel_distance"])
        params["platform_speed"] = _lookup_number(gdm, ["platform_speed", "speed"], params["platform_speed"])
    # 统一 float 默认参数的类型：GDM 传来 int 时补成 float，避免 "99" vs "200.0" 风格不一致
    for _key, _default in tpl["defaults"].items():
        if isinstance(_default, float) and isinstance(params.get(_key), int):
            params[_key] = float(params[_key])
    return params


def render_template(kind: str, params: Dict[str, Any]) -> str:
    """用参数填充模板占位符（{{param}}），未提供的键保留默认。"""
    tpl = _TEMPLATES.get(kind)
    if not tpl:
        return ""
    text = tpl["template"]()
    for key, value in params.items():
        token = "{{%s}}" % key
        text = text.replace(token, str(value))
    return text


def build_artifact(
    kind: str,
    gdm: Dict[str, Any],
    task: Dict[str, Any],
    engine: str = "godot",
) -> Optional[Dict[str, Any]]:
    """渲染参数化组件并产出 artifact（与 code_generator 的 artifact 契约一致）。"""
    tpl = _TEMPLATES.get(kind)
    if not tpl:
        return None
    params = extract_params(kind, gdm or {}, task or {})
    content = render_template(kind, params)
    if not content:
        return None
    return {
        "file_path": tpl["file_path"],
        "content": content,
        "language": "gdscript",
        "engine": engine,
        "metadata": {
            "source_task": (task or {}).get("id", ""),
            "dependencies": [],
            "target_game_object": "",
            "required_components": (task or {}).get("required_components", []),
            "from_template": kind,
        },
    }


def supported_kinds() -> List[str]:
    return list(_TEMPLATES.keys())