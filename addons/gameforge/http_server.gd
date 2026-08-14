## GameForge 内嵌 HTTP 服务器
## 接收 Python 后端推送的代码和场景数据
@tool
extends Node

## 信号：收到文件导入请求
signal files_import_requested(files: Dictionary)
## 信号：收到编译请求
signal compile_requested()
## 信号：收到场景构建请求
signal scene_build_requested(scene_desc: Dictionary)

## HTTP 服务器端口
var port: int = 8765
## TCP 服务器
var _tcp_server: TCPServer
## 是否正在运行
var _running: bool = false
## 活跃连接
var _connections: Array[StreamPeerTCP] = []
const BIND_ADDRESS := "127.0.0.1"
const MAX_CONNECTIONS := 20
const MAX_REQUEST_BYTES := 1_048_576


func _ready() -> void:
	# 从设置读取端口
	var settings = preload("res://addons/gameforge/settings.gd").new()
	port = settings.get_value("http_port", 8765)
	start()


func _exit_tree() -> void:
	stop()


func start() -> void:
	"""启动 HTTP 服务器"""
	_tcp_server = TCPServer.new()
	var err = _tcp_server.listen(port, BIND_ADDRESS)
	if err == OK:
		_running = true
		print("[GameForge] HTTP 服务器已启动，端口: ", port)
	else:
		push_error("[GameForge] HTTP 服务器启动失败: ", err)


func stop() -> void:
	"""停止 HTTP 服务器"""
	_running = false
	if _tcp_server:
		_tcp_server.stop()
	for conn in _connections:
		conn.disconnect_from_host()
	_connections.clear()


func _process(_delta: float) -> void:
	if not _running or not _tcp_server:
		return

	# 接受新连接
	if _tcp_server.is_connection_available():
		var conn = _tcp_server.take_connection()
		if conn and _connections.size() < MAX_CONNECTIONS:
			_connections.append(conn)
		elif conn:
			conn.disconnect_from_host()

	# 处理连接
	var to_remove: Array = []
	for conn in _connections:
		if conn.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			to_remove.append(conn)
			continue

		# 读取 HTTP 请求
		var available = conn.get_available_bytes()
		if available > 0:
			if available > MAX_REQUEST_BYTES:
				_send_response(conn, {"status": 413, "body": {"error": "Request too large"}})
				to_remove.append(conn)
				continue
			var data = conn.get_utf8_string(available)
			_handle_request(conn, data)

	for conn in to_remove:
		_connections.erase(conn)


func _handle_request(conn: StreamPeerTCP, raw_request: String) -> void:
	"""处理 HTTP 请求"""
	var lines = raw_request.split("\r\n")
	if lines.is_empty():
		return

	var request_line = lines[0].split(" ")
	if request_line.size() < 2:
		return

	var method = request_line[0]
	var path = request_line[1]
	var route_path = path.get_slice("?", 0)

	if route_path != "/api/health":
		var token = _get_api_token()
		if token.is_empty():
			_send_response(conn, {"status": 503, "body": {"error": "Godot HTTP token is not configured"}})
			return
		if _get_header(raw_request, "x-api-key") != token:
			_send_response(conn, {"status": 401, "body": {"error": "Unauthorized"}})
			return

	# 解析请求体
	var body = ""
	var body_start = raw_request.find("\r\n\r\n")
	if body_start >= 0:
		body = raw_request.substr(body_start + 4)

	# 路由处理
	var response: Dictionary
	match route_path:
		"/api/health":
			response = _handle_health()
		"/api/import":
			response = _handle_import(body)
		"/api/compile":
			response = _handle_compile()
		"/api/compile/errors":
			response = _handle_compile_errors()
		"/api/scene/generate":
			response = _handle_scene_generate(body)
		"/api/project/reload":
			response = _handle_project_reload()
		_:
			response = {"status": 404, "body": {"error": "Not Found"}}

	_send_response(conn, response)


func _handle_health() -> Dictionary:
	"""健康检查"""
	var version_info = Engine.get_version_info()
	return {
		"status": 200,
		"body": {
			"status": "ok",
			"engine": "godot",
			"version": "%d.%d.%d" % [version_info.major, version_info.minor, version_info.patch],
			"plugin_version": "0.4.0",
		}
	}


func _get_api_token() -> String:
	var environment_token = OS.get_environment("GAMEFORGE_GODOT_HTTP_TOKEN").strip_edges()
	if not environment_token.is_empty():
		return environment_token
	var settings = preload("res://addons/gameforge/settings.gd").new()
	return str(settings.get_value("http_api_token", "")).strip_edges()


func _get_header(request: String, header_name: String) -> String:
	var expected = header_name.to_lower() + ":"
	for line in request.split("\r\n"):
		if line.to_lower().begins_with(expected):
			return line.substr(expected.length()).strip_edges()
	return ""


func _safe_project_path(input_path: String) -> String:
	var relative_path = input_path.strip_edges().replace("\\", "/")
	if relative_path.begins_with("res://"):
		relative_path = relative_path.substr(6)
	if relative_path.is_empty() or relative_path.begins_with("/") or relative_path.contains(".."):
		return ""
	return "res://" + relative_path


func _handle_import(body: String) -> Dictionary:
	"""处理文件导入请求"""
	var json = JSON.new()
	var err = json.parse(body)
	if err != OK:
		return {"status": 400, "body": {"error": "Invalid JSON"}}

	var data = json.data
	if not data is Dictionary or not data.has("files"):
		return {"status": 400, "body": {"error": "Missing 'files' field"}}

	var files: Dictionary = data["files"]
	files_import_requested.emit(files)

	# 写入文件
	var imported: Array = []
	var errors: Array = []
	for path in files:
		var file_path = _safe_project_path(str(path))
		if file_path.is_empty():
			errors.append(str(path) + ": invalid project-relative path")
			continue
		var dir = DirAccess.open("res://")
		if dir:
			var dir_path = file_path.get_base_dir()
			dir.make_dir_recursive(dir_path)

		var file = FileAccess.open(file_path, FileAccess.WRITE)
		if file:
			file.store_string(files[path])
			file.close()
			imported.append(path)
		else:
			errors.append(path + ": 写入失败")

	# 刷新资源系统
	EditorInterface.get_resource_filesystem().scan()

	return {
		"status": 200,
		"body": {
			"status": "success" if errors.is_empty() else "partial",
			"imported": imported,
			"errors": errors,
		}
	}


func _handle_compile() -> Dictionary:
	"""处理编译请求"""
	compile_requested.emit()

	# 触发 Godot 重新扫描和编译
	EditorInterface.get_resource_filesystem().scan()

	# 等待编译完成（通过信号或轮询）
	return {
		"status": 200,
		"body": {
			"status": "success",
			"message": "编译已触发",
		}
	}


func _handle_compile_errors() -> Dictionary:
	"""获取编译错误"""
	# Godot 4.x 没有直接的 API 获取编译错误
	# 需要通过自定义脚本或日志解析
	return {
		"status": 200,
		"body": {
			"errors": [],
			"message": "请查看 Godot 编辑器底部的错误面板",
		}
	}


func _handle_scene_generate(body: String) -> Dictionary:
	"""处理场景构建请求。优先落盘 Python 侧生成的合法 .tscn 文本，回退到 GDScript 构建。"""
	var json = JSON.new()
	var err = json.parse(body)
	if err != OK:
		return {"status": 400, "body": {"error": "Invalid JSON"}}

	var data = json.data
	if not data is Dictionary:
		return {"status": 400, "body": {"error": "Invalid scene description"}}

	scene_build_requested.emit(data)

	# 优先：Python 已构建合法 .tscn 文本，直接落盘（绕开 GDScript 端类型错配）
	if data.has("tscn") and data["tscn"] is String and data["tscn"].length() > 0:
		var scene_name = data.get("scene_name", "GameScene")
		var scene_path = "res://scenes/%s.tscn" % scene_name
		var dir = DirAccess.open("res://")
		if dir:
			dir.make_dir_recursive("res://scenes")
		var f = FileAccess.open(scene_path, FileAccess.WRITE)
		if f:
			f.store_string(data["tscn"])
			f.close()
			EditorInterface.get_resource_filesystem().scan()
			return {"status": 200, "body": {"status": "success", "scene_path": scene_path, "object_count": 0}}
		else:
			return {"status": 500, "body": {"error": "无法写入场景文件: %s" % scene_path}}

	# 回退：旧版 scene_desc 构建（兼容）
	var result = _build_scene(data)
	return {"status": 200, "body": result}


func _handle_project_reload() -> Dictionary:
	"""重新加载项目"""
	EditorInterface.get_resource_filesystem().scan()
	return {
		"status": 200,
		"body": {"status": "ok", "message": "项目已重新加载"}
	}


func _build_scene(scene_desc: Dictionary) -> Dictionary:
	"""构建 Godot 场景"""
	var scene_name = scene_desc.get("scene_name", "GameScene")
	var is_2d = scene_desc.get("is_2d", true)

	# 创建场景
	var root_type = "Node2D" if is_2d else "Node3D"
	var root = ClassDB.instantiate(root_type)
	root.name = scene_name

	# 添加游戏对象
	var game_objects = scene_desc.get("game_objects", [])
	for obj_desc in game_objects:
		var node = _create_node(obj_desc, is_2d)
		if node:
			root.add_child(node)
			node.owner = root

	# 保存场景
	var scene_path = "res://scenes/%s.tscn" % scene_name
	var dir = DirAccess.open("res://")
	if dir:
		dir.make_dir_recursive("res://scenes")

	var scene = PackedScene.new()
	var pack_result = scene.pack(root)
	if pack_result == OK:
		ResourceSaver.save(scene, scene_path)
		root.queue_free()
		EditorInterface.get_resource_filesystem().scan()
		return {
			"status": "success",
			"scene_path": scene_path,
			"object_count": game_objects.size(),
		}
	else:
		root.queue_free()
		return {
			"status": "error",
			"error": "场景打包失败",
		}


func _create_node(obj_desc: Dictionary, is_2d: bool) -> Node:
	"""创建节点"""
	var node_type = obj_desc.get("type", "Node2D" if is_2d else "Node3D")
	var node_name = obj_desc.get("name", "Node")

	# 实例化节点
	var node: Node
	if ClassDB.class_exists(node_type):
		node = ClassDB.instantiate(node_type)
	else:
		# 尝试作为脚本加载
		push_warning("[GameForge] 未知节点类型: ", node_type)
		node = Node2D.new() if is_2d else Node3D.new()

	node.name = node_name

	# 设置位置
	var pos = obj_desc.get("position", [0, 0, 0])
	if is_2d and node is Node2D:
		node.position = Vector2(pos[0], pos[1])
	elif not is_2d and node is Node3D:
		node.position = Vector3(pos[0], pos[1], pos[2])

	# 设置子节点
	var children = obj_desc.get("children", [])
	for child_desc in children:
		var child = _create_node(child_desc, is_2d)
		if child:
			node.add_child(child)
			child.owner = node

	return node


func _send_response(conn: StreamPeerTCP, response: Dictionary) -> void:
	"""发送 HTTP 响应"""
	var status = response.get("status", 200)
	var body = response.get("body", {})
	var body_json = JSON.stringify(body)

	var status_text = "OK" if status == 200 else "Error"
	var headers = [
		"HTTP/1.1 %d %s" % [status, status_text],
		"Content-Type: application/json",
		"Content-Length: %d" % body_json.length(),
		"",
		"",
	]

	var response_text = "\r\n".join(headers) + body_json
	# 直接写原始字节：put_utf8_string 会附加 4 字节长度前缀，破坏标准 HTTP 客户端解析
	conn.put_data(response_text.to_utf8_buffer())
