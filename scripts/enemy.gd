## Enemy — 敌人控制器
## 巡逻型敌人 AI
extends Node2D

## 移动速度
@export var speed: float = 100.0
## 巡逻范围
@export var patrol_distance: float = 200.0
## 是否面向右
@export var face_right: bool = true

## 巡逻起始位置
var _start_position: Vector2
## 巡逻方向
var _direction: int = 1

## 节点引用
@onready var animated_sprite: AnimatedSprite2D = $AnimatedSprite2D
@onready var patrol_area: Area2D = $PatrolArea


func _ready() -> void:
	_start_position = global_position
	if not face_right:
		_direction = -1
		if animated_sprite:
			animated_sprite.flip_h = true


func _process(delta: float) -> void:
	_patrol(delta)


## 巡逻行为
func _patrol(delta: float) -> void:
	var distance_from_start = global_position.x - _start_position.x

	# 到达巡逻边界，转向
	if abs(distance_from_start) >= patrol_distance:
		_direction *= -1
		if animated_sprite:
			animated_sprite.flip_h = _direction < 0

	# 移动
	position.x += speed * _direction * delta


## 当玩家进入检测区域
func _on_patrol_area_body_entered(body: Node2D) -> void:
	if body.is_in_group("player"):
		# 可以在这里添加追击逻辑
		pass


## 当玩家离开检测区域
func _on_patrol_area_body_exited(body: Node2D) -> void:
	if body.is_in_group("player"):
		# 恢复巡逻
		pass
