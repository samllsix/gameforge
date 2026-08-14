## ExamplePatrolComponent — 示例巡逻 AI 组件
## 演示如何使用 AIComponent 基类实现巡逻行为
class_name ExamplePatrolComponent
extends "res://addons/ai_native/ai_component.gd"


## 巡逻速度
@export var patrol_speed: float = 100.0
## 巡逻范围
@export var patrol_distance: float = 200.0
## 是否面向右
@export var face_right: bool = true

## 巡逻起始位置
var _start_position: Vector2
## 巡逻方向
var _direction: int = 1
## 当前状态
var _state: String = "patrol"


func _on_attached(node: Node) -> void:
	_start_position = node.global_position if node is Node2D else Vector2.ZERO
	_direction = 1 if face_right else -1


func _on_tick(delta: float, observation: Observation) -> void:
	if not target_node or not target_node is Node2D:
		return

	# 巡逻逻辑
	var distance_from_start = target_node.global_position.x - _start_position.x

	# 到达边界，转向
	if abs(distance_from_start) >= patrol_distance:
		_direction *= -1

	# 移动
	if target_node is CharacterBody2D:
		target_node.velocity.x = patrol_speed * _direction
		target_node.move_and_slide()
	else:
		target_node.position.x += patrol_speed * _direction * delta

	# 翻转精灵
	var sprite = target_node.get_node_or_null("AnimatedSprite2D")
	if sprite:
		sprite.flip_h = _direction < 0

	# 更新观测
	observation.facing_direction = _direction


func _on_action_received(action: Action) -> void:
	match action.type:
		Action.Type.MOVE:
			_direction = 1 if action.move_direction.x > 0 else -1
		Action.Type.CUSTOM:
			if action.custom_action == "set_speed":
				patrol_speed = action.params.get("speed", patrol_speed)
