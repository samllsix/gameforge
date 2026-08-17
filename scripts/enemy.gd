# Enemy — 敌人控制器
# 巡逻型敌人 AI
extends CharacterBody2D

@export var speed: float = 100.0
@export var patrol_distance: float = 200.0
@export var face_right: bool = true

var _start_position: Vector2
var _direction: int = 1
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity")

@onready var mesh: MeshInstance2D = get_node_or_null("Mesh")


func _ready() -> void:
	_start_position = global_position
	if not face_right:
		_direction = -1


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y += _gravity * delta

	var distance_from_start = global_position.x - _start_position.x
	if abs(distance_from_start) >= patrol_distance:
		_direction *= -1
		if mesh:
			mesh.flip_h = _direction < 0

	velocity.x = speed * _direction
	move_and_slide()


func take_damage_from_player() -> void:
	queue_free()
