## Player — 玩家角色控制器
## 2D 平台跳跃角色
extends CharacterBody2D

## 移动速度
@export var speed: float = 300.0
## 跳跃速度
@export var jump_velocity: float = -400.0
## 重力倍率
@export var gravity_scale: float = 1.0

## 信号：收集金币
signal coin_collected()
## 信号：受到伤害
signal damaged()

## 获取重力值
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity")
## 是否存活
var _is_alive: bool = true

## 节点引用
@onready var animated_sprite: AnimatedSprite2D = $AnimatedSprite2D
@onready var collision_shape: CollisionShape2D = $CollisionShape2D


func _ready() -> void:
	print("Player 已初始化")


func _physics_process(delta: float) -> void:
	if not _is_alive:
		return

	# 应用重力
	if not is_on_floor():
		velocity.y += _gravity * gravity_scale * delta

	# 处理跳跃
	if Input.is_action_just_pressed("jump") and is_on_floor():
		velocity.y = jump_velocity

	# 获取输入方向
	var direction := Input.get_axis("move_left", "move_right")

	# 应用移动
	if direction:
		velocity.x = direction * speed
		# 翻转精灵
		if animated_sprite:
			animated_sprite.flip_h = direction < 0
	else:
		velocity.x = move_toward(velocity.x, 0, speed)

	# 移动
	move_and_slide()

	# 更新动画
	_update_animation()


## 更新动画
func _update_animation() -> void:
	if not animated_sprite:
		return

	if not is_on_floor():
		animated_sprite.play("jump")
	elif velocity.x != 0:
		animated_sprite.play("run")
	else:
		animated_sprite.play("idle")


## 受到伤害
func take_damage(amount: int = 1) -> void:
	if not _is_alive:
		return

	damaged.emit()
	GameManager.lose_life()

	if GameManager.lives <= 0:
		_die()


## 死亡
func _die() -> void:
	_is_alive = false
	velocity = Vector2.ZERO
	if animated_sprite:
		animated_sprite.play("idle")
	print("玩家死亡")


## 收集金币
func collect_coin() -> void:
	coin_collected.emit()
	GameManager.add_score(10)
