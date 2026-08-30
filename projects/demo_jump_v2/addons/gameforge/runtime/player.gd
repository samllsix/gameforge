extends CharacterBody2D
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
