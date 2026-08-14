## GameForge UI 面板
## 底部面板界面：输入需求、查看生成进度、预览场景
@tool
extends Control

## 需求输入框
var _requirements_edit: TextEdit
## 生成按钮
var _generate_button: Button
## 状态标签
var _status_label: Label
## 进度条
var _progress_bar: ProgressBar
## 文件列表
var _file_tree: Tree
## 日志输出
var _log_output: RichTextLabel

## WebSocket 客户端引用
var _ws_client: Node


func _ready() -> void:
	_build_ui()
	_connect_signals()


func _build_ui() -> void:
	"""构建 UI"""
	var main_vbox = VBoxContainer.new()
	main_vbox.set_anchors_and_offsets_preset(PRESET_FULL_RECT)
	add_child(main_vbox)

	# 顶部工具栏
	var toolbar = HBoxContainer.new()
	main_vbox.add_child(toolbar)

	var title = Label.new()
	title.text = "GameForge AI"
	title.add_theme_font_size_override("font_size", 18)
	toolbar.add_child(title)

	toolbar.add_child(HSeparator.new())

	var settings_button = Button.new()
	settings_button.text = "设置"
	settings_button.pressed.connect(_on_settings_pressed)
	toolbar.add_child(settings_button)

	# 分割器
	var hsplit = HSplitContainer.new()
	hsplit.size_flags_vertical = Control.SIZE_EXPAND_FILL
	main_vbox.add_child(hsplit)

	# 左侧：输入区域
	var left_panel = VBoxContainer.new()
	left_panel.custom_minimum_size = Vector2(300, 0)
	hsplit.add_child(left_panel)

	var req_label = Label.new()
	req_label.text = "游戏需求描述："
	left_panel.add_child(req_label)

	_requirements_edit = TextEdit.new()
	_requirements_edit.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_requirements_edit.placeholder_text = "例如：创建一个2D平台跳跃游戏，玩家可以左右移动和跳跃，有金币可以收集..."
	left_panel.add_child(_requirements_edit)

	var button_row = HBoxContainer.new()
	left_panel.add_child(button_row)

	_generate_button = Button.new()
	_generate_button.text = "生成代码"
	_generate_button.pressed.connect(_on_generate_pressed)
	button_row.add_child(_generate_button)

	_progress_bar = ProgressBar.new()
	_progress_bar.visible = false
	_progress_bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button_row.add_child(_progress_bar)

	_status_label = Label.new()
	_status_label.text = "就绪"
	left_panel.add_child(_status_label)

	# 右侧：输出区域
	var right_panel = VBoxContainer.new()
	right_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hsplit.add_child(right_panel)

	var tab_container = TabContainer.new()
	tab_container.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right_panel.add_child(tab_container)

	# 文件列表
	_file_tree = Tree.new()
	_file_tree.columns = 2
	_file_tree.set_column_title(0, "文件")
	_file_tree.set_column_title(1, "状态")
	tab_container.add_child(_file_tree)
	tab_container.set_tab_title(0, "生成文件")

	# 日志输出
	_log_output = RichTextLabel.new()
	_log_output.bbcode_enabled = true
	_log_output.size_flags_vertical = Control.SIZE_EXPAND_FILL
	tab_container.add_child(_log_output)
	tab_container.set_tab_title(1, "日志")


func _connect_signals() -> void:
	"""连接信号"""
	# 在 _ready 之后延迟连接 WebSocket 信号
	call_deferred("_try_connect_ws")


func _try_connect_ws() -> void:
	"""尝试连接 WebSocket"""
	var ws_nodes = get_tree().get_nodes_in_group("GameForgeWSClient")
	if ws_nodes.is_empty():
		# 尝试从父节点获取
		var parent = get_parent()
		while parent:
			if parent.has_method("get") and parent.get("name") == "GameForgeWSClient":
				_ws_client = parent
				break
			parent = parent.get_parent()

	if not _ws_client:
		# 查找同级节点
		for child in get_parent().get_children() if get_parent() else []:
			if child.name == "GameForgeWSClient":
				_ws_client = child
				break

	if _ws_client:
		if _ws_client.has_signal("generation_progress"):
			_ws_client.generation_progress.connect(_on_progress)
		if _ws_client.has_signal("code_file_generated"):
			_ws_client.code_file_generated.connect(_on_code_file)
		if _ws_client.has_signal("generation_complete"):
			_ws_client.generation_complete.connect(_on_complete)
		if _ws_client.has_signal("connected"):
			_ws_client.connected.connect(_on_ws_connected)
		if _ws_client.has_signal("disconnected"):
			_ws_client.disconnected.connect(_on_ws_disconnected)
		_log("[color=green]WebSocket 客户端已连接[/color]")
	else:
		_log("[color=yellow]WebSocket 客户端未找到，请确保插件已正确加载[/color]")


func _on_generate_pressed() -> void:
	"""生成按钮点击"""
	var requirements = _requirements_edit.text.strip_edges()
	if requirements.is_empty():
		_status_label.text = "请输入游戏需求"
		return

	_generate_button.disabled = true
	_progress_bar.visible = true
	_progress_bar.value = 0
	_status_label.text = "正在生成..."
	_file_tree.clear()
	_log("[color=cyan]开始生成: " + requirements + "[/color]")

	# 通过 HTTP API 发送请求
	_send_http_request(requirements)


func _send_http_request(requirements: String) -> void:
	"""通过 HTTP 发送生成请求"""
	var http = HTTPRequest.new()
	add_child(http)
	http.request_completed.connect(_on_http_response.bind(http))

	var settings = preload("res://addons/gameforge/settings.gd").new()
	var backend_url = settings.get_backend_url()
	var api_key = settings.get_api_key()

	var headers = ["Content-Type: application/json"]
	if api_key:
		headers.append("X-API-Key: " + api_key)

	var body = JSON.stringify({
		"requirements": requirements,
		"engine": "godot",
		"project_name": "GameForge",
	})

	var err = http.request(
		backend_url + "/api/v1/generate_stream",
		headers,
		HTTPClient.METHOD_POST,
		body
	)

	if err != OK:
		_status_label.text = "请求失败"
		_generate_button.disabled = false
		_log("[color=red]HTTP 请求失败: %d[/color]" % err)
		http.queue_free()


func _on_http_response(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray, http: HTTPRequest) -> void:
	"""HTTP 响应处理"""
	http.queue_free()

	if response_code != 200:
		_status_label.text = "请求失败: %d" % response_code
		_generate_button.disabled = false
		_log("[color=red]服务器返回错误: %d[/color]" % response_code)
		return

	# SSE 流式响应需要不同的处理方式
	_status_label.text = "请求已提交，请等待..."
	_log("[color=green]请求已提交[/color]")

	# 如果是同步响应，直接处理
	var response_text = body.get_string_from_utf8()
	if response_text.begins_with("{"):
		var json = JSON.new()
		var err = json.parse(response_text)
		if err == OK and json.data is Dictionary:
			var data = json.data
			if data.get("success", false):
				_status_label.text = "生成完成"
				var files = data.get("code_generated", {})
				for path in files:
					_add_file_to_tree(path, "已生成")
				_log("[color=green]生成完成，共 %d 个文件[/color]" % files.size())

	_generate_button.disabled = false
	_progress_bar.visible = false


func _on_progress(phase: String, message: String) -> void:
	"""生成进度更新"""
	_status_label.text = message
	_log("[color=yellow][%s][/color] %s" % [phase, message])


func _on_code_file(file_path: String, content: String) -> void:
	"""代码文件生成"""
	_add_file_to_tree(file_path, "已生成")
	_progress_bar.value = min(_progress_bar.value + 5, 90)


func _on_complete(files: Dictionary) -> void:
	"""生成完成"""
	_status_label.text = "生成完成！共 %d 个文件" % files.size()
	_progress_bar.value = 100
	_generate_button.disabled = false
	_log("[color=green]生成完成！[/color]")

	# 延迟隐藏进度条
	await get_tree().create_timer(2.0).timeout
	_progress_bar.visible = false


func _on_ws_connected() -> void:
	"""WebSocket 连接成功"""
	_log("[color=green]WebSocket 已连接到后端[/color]")


func _on_ws_disconnected() -> void:
	"""WebSocket 断开"""
	_log("[color=yellow]WebSocket 已断开[/color]")


func _on_settings_pressed() -> void:
	"""设置按钮点击"""
	_log("[color=cyan]设置功能开发中...[/color]")


func _add_file_to_tree(file_path: String, status: String) -> void:
	"""添加文件到树"""
	var root = _file_tree.get_root()
	if not root:
		root = _file_tree.create_item()

	var item = _file_tree.create_item(root)
	item.set_text(0, file_path)
	item.set_text(1, status)


func _log(message: String) -> void:
	"""输出日"""
	if _log_output:
		_log_output.append_text(message + "\n")
