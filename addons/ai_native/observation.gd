## Observation — AI 观测数据结构
## 描述当前帧的环境状态，供 AI 组件决策使用
class_name Observation
extends RefCounted

## 时间戳
var timestamp: float = 0.0

## 目标节点位置
var position: Vector2 = Vector2.ZERO

## 目标节点速度
var velocity: Vector2 = Vector2.ZERO

## 是否在地面上（2D 角色）
var is_on_floor: bool = false

## 朝向（1 = 右，-1 = 左）
var facing_direction: int = 1

## 当前生命值
var health: int = 0

## 最大生命值
var max_health: int = 0

## 当前分数
var score: int = 0

## 可见的实体列表（类型、位置、距离）
var visible_entities: Array[Dictionary] = []

## 可交互的对象列表
var interactable_objects: Array[Dictionary] = []

## 自定义扩展数据（子类可填充任意 KV）
var extra: Dictionary = {}


## 获取到目标点的方向向量
func direction_to(target_pos: Vector2) -> Vector2:
	return (target_pos - position).normalized()


## 获取到目标点的距离
func distance_to(target_pos: Vector2) -> float:
	return position.distance_to(target_pos)


## 是否在指定范围内
func is_within_range(target_pos: Vector2, range: float) -> bool:
	return distance_to(target_pos) <= range


## 获取最近的实体
func get_nearest_entity(entities: Array[Dictionary] = []) -> Dictionary:
	var source = entities if not entities.is_empty() else visible_entities
	if source.is_empty():
		return {}

	var nearest: Dictionary = {}
	var min_dist: float = INF

	for entity in source:
		var entity_pos: Vector2 = entity.get("position", Vector2.ZERO)
		var dist = distance_to(entity_pos)
		if dist < min_dist:
			min_dist = dist
			nearest = entity

	return nearest


## 转换为字典（用于序列化/传输）
func to_dict() -> Dictionary:
	return {
		"timestamp": timestamp,
		"position": {"x": position.x, "y": position.y},
		"velocity": {"x": velocity.x, "y": velocity.y},
		"is_on_floor": is_on_floor,
		"facing_direction": facing_direction,
		"health": health,
		"max_health": max_health,
		"score": score,
		"visible_entities": visible_entities,
		"interactable_objects": interactable_objects,
		"extra": extra,
	}


## 从字典创建
static func from_dict(data: Dictionary):
	var obs = new()
	obs.timestamp = data.get("timestamp", 0.0)

	var pos = data.get("position", {})
	obs.position = Vector2(pos.get("x", 0), pos.get("y", 0))

	var vel = data.get("velocity", {})
	obs.velocity = Vector2(vel.get("x", 0), vel.get("y", 0))

	obs.is_on_floor = data.get("is_on_floor", false)
	obs.facing_direction = data.get("facing_direction", 1)
	obs.health = data.get("health", 0)
	obs.max_health = data.get("max_health", 0)
	obs.score = data.get("score", 0)
	obs.visible_entities = data.get("visible_entities", [])
	obs.interactable_objects = data.get("interactable_objects", [])
	obs.extra = data.get("extra", {})

	return obs
