# Player — 玩家角色控制器
# 2D 平台跳跃角色
extends CharacterBody2D

@export var speed: float = 300.0
@export var jump_velocity: float = -400.0
@export var gravity_scale: float = 1.0

signal coin_collected()
signal damaged()

var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity")
var _is_alive: bool = true
var _score_manager: Node = null

@onready var mesh: MeshInstance2D = get_node_or_null("Mesh")
@onready var collision_shape: CollisionShape2D = get_node_or_null("CollisionShape")


func _ready() -> void:
	_score_manager = get_tree().current_scene.get_node_or_null("ScoreManager")
	print("Player 已初始化")


func _physics_process(delta: float) -> void:
	if not _is_alive:
		return

	if not is_on_floor():
		velocity.y += _gravity * gravity_scale * delta

	if Input.is_action_just_pressed("jump") and is_on_floor():
		velocity.y = jump_velocity

	var direction := Input.get_axis("move_left", "move_right")

	if direction:
		velocity.x = direction * speed
		if mesh:
			mesh.flip_h = direction < 0
	else:
		velocity.x = move_toward(velocity.x, 0, speed)

	move_and_slide()


func take_damage(amount: int = 1) -> void:
	if not _is_alive:
		return
	damaged.emit()
	if _score_manager and _score_manager.has_method("add_score"):
		pass
	_die()


func _die() -> void:
	_is_alive = false
	velocity = Vector2.ZERO
	print("玩家死亡")


func collect_coin() -> void:
	coin_collected.emit()
	if _score_manager and _score_manager.has_method("add_score"):
		_score_manager.add_score(10)
