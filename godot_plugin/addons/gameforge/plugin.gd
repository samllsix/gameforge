## GameForge 编辑器插件入口
## 提供 AI 代码生成、场景构建等功能
@tool
extends EditorPlugin

## 插件名称
const PLUGIN_NAME := "GameForge"

## 底部面板引用
var _panel: Control
## HTTP 服务器
var _http_server: Node
## WebSocket 客户端
var _ws_client: Node


func _enter_tree() -> void:
	print("[GameForge] 插件已启用")

	# 创建 UI 面板
	_panel = _create_panel()
	add_control_to_bottom_panel(_panel, PLUGIN_NAME)

	# 初始化 HTTP 服务器（接收 Python 后端推送）
	_http_server = preload("res://addons/gameforge/http_server.gd").new()
	_http_server.name = "GameForgeHTTPServer"
	add_child(_http_server)

	# 初始化 WebSocket 客户端（连接 Python 后端）
	_ws_client = preload("res://addons/gameforge/websocket_client.gd").new()
	_ws_client.name = "GameForgeWSClient"
	add_child(_ws_client)


func _exit_tree() -> void:
	print("[GameForge] 插件已禁用")

	# 清理
	if _panel:
		remove_control_from_bottom_panel(_panel)
		_panel.queue_free()

	if _http_server:
		_http_server.queue_free()

	if _ws_client:
		_ws_client.queue_free()


func _create_panel() -> Control:
	"""创建底部面板 UI"""
	var panel = preload("res://addons/gameforge/ui_panel.gd").new()
	return panel
