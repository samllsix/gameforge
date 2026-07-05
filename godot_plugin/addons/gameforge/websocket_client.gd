## GameForge WebSocket 客户端
## 连接 Python 后端，接收流式输出和实时推送
@tool
extends Node

## 信号：连接成功
signal connected()
## 信号：连接断开
signal disconnected()
## 信号：收到消息
signal message_received(event_type: String, data: Dictionary)
## 信号：代码生成进度
signal generation_progress(phase: String, message: String)
## 信号：代码文件生成
signal code_file_generated(file_path: String, content: String)
## 信号：生成完成
signal generation_complete(files: Dictionary)

## WebSocket 服务器 URL
var ws_url: String = "ws://localhost:8766"
## 是否自动重连
var auto_reconnect: bool = true
## 重连间隔（秒）
var reconnect_interval: float = 3.0

## WebSocketPeer 引用
var _ws: WebSocketPeer
## 是否已连接
var _connected: bool = false
## 重连定时器
var _reconnect_timer: float = 0.0


func _ready() -> void:
	var settings = preload("res://addons/gameforge/settings.gd").new()
	ws_url = settings.get_ws_url()


func _process(delta: float) -> void:
	if _ws:
		_ws.poll()

		var state = _ws.get_ready_state()
		if state == WebSocketPeer.STATE_OPEN:
			if not _connected:
				_connected = true
				connected.emit()
				print("[GameForge] WebSocket 已连接: ", ws_url)

			# 读取消息
			while _ws.get_available_packet_count():
				var packet = _ws.get_packet()
				var text = packet.get_string_from_utf8()
				_handle_message(text)

		elif state == WebSocketPeer.STATE_CLOSING:
			pass  # 等待关闭
		elif state == WebSocketPeer.STATE_CLOSED:
			if _connected:
				_connected = false
				var code = _ws.get_close_code()
				var reason = _ws.get_close_reason()
				print("[GameForge] WebSocket 已断开: %d %s" % [code, reason])
				disconnected.emit()
				_ws = null

	# 自动重连
	if not _connected and auto_reconnect:
		_reconnect_timer += delta
		if _reconnect_timer >= reconnect_interval:
			_reconnect_timer = 0.0
			connect_to_server()


func connect_to_server(url: String = "") -> void:
	"""连接到 WebSocket 服务器"""
	if url:
		ws_url = url

	if _connected:
		return

	_ws = WebSocketPeer.new()
	var err = _ws.connect_to_url(ws_url)
	if err != OK:
		push_warning("[GameForge] WebSocket 连接失败: ", err)
		_ws = null


func disconnect_from_server() -> void:
	"""断开连接"""
	auto_reconnect = false
	if _ws:
		_ws.close()


func send_message(event_type: String, data: Dictionary) -> bool:
	"""发送消息"""
	if not _connected or not _ws:
		return false

	var message = JSON.stringify({"event": event_type, "data": data})
	_ws.put_text(message)
	return true


func send_generate_request(requirements: String, engine: String = "godot", project_name: String = "GameForge") -> bool:
	"""发送代码生成请求"""
	return send_message("generate", {
		"requirements": requirements,
		"engine": engine,
		"project_name": project_name,
	})


func _handle_message(text: String) -> void:
	"""处理收到的消息"""
	var json = JSON.new()
	var err = json.parse(text)
	if err != OK:
		push_warning("[GameForge] WebSocket 消息解析失败: ", text.substr(0, 100))
		return

	var data = json.data
	if not data is Dictionary:
		return

	var event_type = data.get("event", "unknown")
	var event_data = data.get("data", {})

	message_received.emit(event_type, event_data)

	# 处理特定事件
	match event_type:
		"phase_start":
			generation_progress.emit(event_data.get("phase", ""), event_data.get("message", ""))
		"code_file":
			var file_path = event_data.get("file_path", "")
			var content = event_data.get("content", "")
			if file_path and content:
				code_file_generated.emit(file_path, content)
				if _auto_import_enabled():
					_import_file(file_path, content)
		"complete":
			var files = event_data.get("files", {})
			generation_complete.emit(files)
			print("[GameForge] 代码生成完成，共 ", files.size(), " 个文件")
		"error":
			push_error("[GameForge] 错误: ", event_data.get("message", "未知错误"))
		"compile_result":
			var status = event_data.get("status", "")
			if status == "success":
				print("[GameForge] 编译成功")
			else:
				push_warning("[GameForge] 编译失败: ", event_data.get("message", ""))
		"scene_complete":
			print("[GameForge] 场景已生成: ", event_data.get("scene_path", ""))


func _auto_import_enabled() -> bool:
	"""是否启用自动导入"""
	var settings = preload("res://addons/gameforge/settings.gd").new()
	return settings.get_value("auto_import", true)


func _import_file(file_path: String, content: String) -> void:
	"""导入文件到项目"""
	var full_path = "res://" + file_path
	var dir = DirAccess.open("res://")
	if dir:
		var dir_path = full_path.get_base_dir()
		dir.make_dir_recursive(dir_path)

	var file = FileAccess.open(full_path, FileAccess.WRITE)
	if file:
		file.store_string(content)
		file.close()
		# 刷新资源系统
		EditorInterface.get_resource_filesystem().scan()
	else:
		push_error("[GameForge] 文件写入失败: ", full_path)
