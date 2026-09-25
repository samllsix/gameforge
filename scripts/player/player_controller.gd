## 玩家控制器 — 移动、跳跃
extends CharacterBody2D

# ==================== 信号 ====================
signal health_changed(new_health: int)
signal died()

# ==================== 导出变量 ====================
@export var move_speed: float = 200.0
@export var jump_velocity: float = -400.0

# ==================== 私有变量 ====================
var _health: int = 3
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
