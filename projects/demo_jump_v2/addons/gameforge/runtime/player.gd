extends CharacterBody2D
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
