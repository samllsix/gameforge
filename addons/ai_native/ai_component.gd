## AIComponent — AI 组件基类
## 可挂载到任何 Node 上，为其提供 AI 行为
## 子类实现 tick() 和 receive_action() 即可
class_name AIComponent
extends Node

## 组件是否激活
@export var enabled: bool = true
## 组件优先级（数值越小越先执行）
@export var priority: int = 0

## 挂载的目标节点（自动设置）
var target_node: Node = null

## 当前观测数据
var _current_observation: Observation = null


func _ready() -> void:
	# 自动注册到 AIController
	_register_to_controller()


func _exit_tree() -> void:
	# 自动从 AIController 注销
	_unregister_from_controller()


## 挂载到目标节点
## 通常在创建组件时调用，或由 AIController 自动设置
func attach(node: Node) -> void:
	target_node = node
	_on_attached(node)


## 分离目标节点
func detach() -> void:
	var old_node = target_node
	target_node = null
	_on_detached(old_node)


## 每帧调度（由 AIController 调用）
func tick(delta: float) -> void:
	if not enabled or not target_node:
		return

	# 1. 收集观测
	_current_observation = send_observation()

	# 2. 子类实现决策和行为
	_on_tick(delta, _current_observation)


## 发送观测数据（子类重写此方法提供环境信息）
func send_observation() -> Observation:
	var obs = Observation.new()
	obs.position = _get_target_position()
	obs.velocity = _get_target_velocity()
	obs.extra = _get_custom_observation()
	return obs


## 接收行为指令（由外部系统调用，如 LLM、行为树等）
func receive_action(action: Action) -> void:
	if not enabled or not target_node:
		return
	_on_action_received(action)


## 获取当前观测
func get_observation() -> Observation:
	return _current_observation


# ========== 子类重写方法 ==========

## 组件挂载时回调
func _on_attached(node: Node) -> void:
	pass


## 组件分离时回调
func _on_detached(node: Node) -> void:
	pass


## 每帧逻辑（子类必须实现）
func _on_tick(delta: float, observation: Observation) -> void:
	pass


## 收到行为指令时回调
func _on_action_received(action: Action) -> void:
	pass


## 获取自定义观测数据（子类重写）
func _get_custom_observation() -> Dictionary:
	return {}


# ========== 辅助方法 ==========

## 获取目标位置
func _get_target_position() -> Vector2:
	if not target_node:
		return Vector2.ZERO
	if target_node is Node2D:
		return target_node.global_position
	return Vector2.ZERO


## 获取目标速度
func _get_target_velocity() -> Vector2:
	if not target_node:
		return Vector2.ZERO
	if target_node is CharacterBody2D:
		return target_node.velocity
	return Vector2.ZERO


## 注册到 AIController
func _register_to_controller() -> void:
	var controller = get_node_or_null("/root/AIController")
	if controller and controller.has_method("register_component"):
		controller.register_component(self)


## 从 AIController 注销
func _unregister_from_controller() -> void:
	var controller = get_node_or_null("/root/AIController")
	if controller and controller.has_method("unregister_component"):
		controller.unregister_component(self)
