## AIController — AI 控制器（Autoload 单例）
## 统一管理所有 AIComponent，每帧调度 tick
extends Node

## 信号：组件注册
signal component_registered(component: AIComponent)
## 信号：组件注销
signal component_unregistered(component: AIComponent)
## 信号：组件发送行为
signal action_dispatched(component: AIComponent, action: Action)

## 所有注册的 AI 组件（按优先级排序）
var _components: Array[AIComponent] = []
## 组件是否需要重新排序
var _dirty_sort: bool = false
## 是否启用全局 AI 调度
var enabled: bool = true
## 调试模式
var debug_mode: bool = false


func _ready() -> void:
	print("[AIController] 已初始化")


func _process(delta: float) -> void:
	if not enabled:
		return

	# 按优先级排序（如果需要）
	if _dirty_sort:
		_sort_components()
		_dirty_sort = false

	# 调度所有组件
	for component in _components:
		if is_instance_valid(component) and component.enabled:
			component.tick(delta)


## 注册组件
func register_component(component: AIComponent) -> void:
	if component in _components:
		return

	_components.append(component)
	_dirty_sort = true
	component_registered.emit(component)

	if debug_mode:
		print("[AIController] 注册组件: ", component.name, " -> ", component.target_node.name if component.target_node else "null")


## 注销组件
func unregister_component(component: AIComponent) -> void:
	var idx = _components.find(component)
	if idx == -1:
		return

	_components.remove_at(idx)
	component_unregistered.emit(component)

	if debug_mode:
		print("[AIController] 注销组件: ", component.name)


## 获取所有组件
func get_components() -> Array[AIComponent]:
	return _components


## 获取指定类型的所有组件
func get_components_of_type(type: String) -> Array[AIComponent]:
	var result: Array[AIComponent] = []
	for component in _components:
		if is_instance_valid(component) and component.is_class(type):
			result.append(component)
	return result


## 获取挂载到指定节点的组件
func get_components_for_node(node: Node) -> Array[AIComponent]:
	var result: Array[AIComponent] = []
	for component in _components:
		if is_instance_valid(component) and component.target_node == node:
			result.append(component)
	return result


## 向指定组件发送行为
func dispatch_action(component: AIComponent, action: Action) -> void:
	if not is_instance_valid(component):
		return
	component.receive_action(action)
	action_dispatched.emit(component, action)


## 向所有组件广播行为
func broadcast_action(action: Action) -> void:
	for component in _components:
		if is_instance_valid(component) and component.enabled:
			component.receive_action(action)
	action_dispatched.emit(null, action)


## 获取所有组件的观测数据
func collect_observations() -> Array[Observation]:
	var observations: Array[Observation] = []
	for component in _components:
		if is_instance_valid(component) and component.enabled:
			var obs = component.send_observation()
			if obs:
				observations.append(obs)
	return observations


## 获取全局状态快照（用于调试或序列化）
func get_state_snapshot() -> Dictionary:
	var snapshot = {
		"component_count": _components.size(),
		"enabled": enabled,
		"components": [],
	}

	for component in _components:
		if is_instance_valid(component):
			snapshot.components.append({
				"name": component.name,
				"enabled": component.enabled,
				"priority": component.priority,
				"target": component.target_node.name if component.target_node else "null",
			})

	return snapshot


## 按优先级排序组件
func _sort_components() -> void:
	_components.sort_custom(_compare_priority)


## 优先级比较函数
func _compare_priority(a: AIComponent, b: AIComponent) -> bool:
	return a.priority < b.priority
