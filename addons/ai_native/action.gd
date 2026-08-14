## Action — AI 行为输出数据结构
## 描述 AI 组件应该执行的行为指令
class_name Action
extends RefCounted

## 行为类型枚举
enum Type {
	NONE,           ## 无操作
	MOVE,           ## 移动
	JUMP,           ## 跳跃
	ATTACK,         ## 攻击
	INTERACT,       ## 交互
	USE_ITEM,       ## 使用物品
	SPEAK,          ## 说话
	WAIT,           ## 等待
	CUSTOM,         ## 自定义行为
}

## 行为类型
var type: Type = Type.NONE

## 移动方向（MOVE 类型）
var move_direction: Vector2 = Vector2.ZERO

## 目标位置（MOVE 类型，可选）
var target_position: Vector2 = Vector2.ZERO

## 是否持续执行
var continuous: bool = false

## 持续时间（秒，0 = 无限）
var duration: float = 0.0

## 行为强度（0.0 ~ 1.0）
var intensity: float = 1.0

## 目标对象引用
var target_node: Node = null

## 自定义行为名称（CUSTOM 类型）
var custom_action: String = ""

## 自定义参数
var params: Dictionary = {}


## 静态工厂方法：创建移动行为
static func move(direction: Vector2, continuous: bool = true):
	var action = new()
	action.type = Type.MOVE
	action.move_direction = direction.normalized()
	action.continuous = continuous
	return action


## 静态工厂方法：创建跳跃行为
static func jump():
	var action = new()
	action.type = Type.JUMP
	return action


## 静态工厂方法：创建攻击行为
static func attack(target: Node = null, intensity: float = 1.0):
	var action = new()
	action.type = Type.ATTACK
	action.target_node = target
	action.intensity = intensity
	return action


## 静态工厂方法：创建交互行为
static func interact(target: Node = null):
	var action = new()
	action.type = Type.INTERACT
	action.target_node = target
	return action


## 静态工厂方法：创建等待行为
static func wait(duration: float):
	var action = new()
	action.type = Type.WAIT
	action.duration = duration
	return action


## 静态工厂方法：创建自定义行为
static func custom(action_name: String, params: Dictionary = {}):
	var action = new()
	action.type = Type.CUSTOM
	action.custom_action = action_name
	action.params = params
	return action


## 静态工厂方法：创建无操作
static func none():
	var action = new()
	action.type = Type.NONE
	return action


## 转换为字典（用于序列化/传输）
func to_dict() -> Dictionary:
	return {
		"type": type,
		"move_direction": {"x": move_direction.x, "y": move_direction.y},
		"target_position": {"x": target_position.x, "y": target_position.y},
		"continuous": continuous,
		"duration": duration,
		"intensity": intensity,
		"custom_action": custom_action,
		"params": params,
	}


## 从字典创建
static func from_dict(data: Dictionary):
	var action = new()
	action.type = data.get("type", Type.NONE)

	var dir = data.get("move_direction", {})
	action.move_direction = Vector2(dir.get("x", 0), dir.get("y", 0))

	var pos = data.get("target_position", {})
	action.target_position = Vector2(pos.get("x", 0), pos.get("y", 0))

	action.continuous = data.get("continuous", false)
	action.duration = data.get("duration", 0.0)
	action.intensity = data.get("intensity", 1.0)
	action.custom_action = data.get("custom_action", "")
	action.params = data.get("params", {})

	return action
