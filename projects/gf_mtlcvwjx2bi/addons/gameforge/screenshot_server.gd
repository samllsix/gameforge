## GameForge 实时预览截图服务器（长驻服务）
## 在 Godot 主场景根上挂这个 Node，启动后监听 127.0.0.1:<port>，
## 接收 Python 后端的 HTTP 反向请求，把当前 Viewport 截成 PNG 返回。
##
## 端点：
##   GET /health                 → {"status":"ok",...} 无需鉴权
##   GET /screenshot?frame=N     → image/png，鉴权 X-API-Key
##   GET /frame/advance?frame=N  → 推进虚拟时间到 frame=N，返回 JSON
##
## 设计要点：
##   - 仅监听 loopback，不暴露公网
##   - token 来自环境变量 GAMEFORGE_PREVIEW_TOKEN 或 config.cfg 的 screenshot_token
##   - dummy 渲染器下退回占位图，避免空 PNG 导致客户端崩
@tool
extends Node

const PORT_DEFAULT := 8769
const TOKEN_DEFAULT := "gf_screenshot_local"

var tcp_server: TCPServer
var port: int = PORT_DEFAULT
var token: String = TOKEN_DEFAULT

var current_frame: int = 0
var simulated_dt_total: float = 0.0  ## 累积虚拟时间（秒）
var fps_target: float = 60.0        ## 帧率，用于 dt→frame 换算

## 由 preview_runner.gd 注入：承载主场景的 SubViewport
## headless 下唯一能拿到 viewport texture 的容器
var sub_viewport: SubViewport = null

var _running: bool = false


func _ready() -> void:
	# 从环境变量 / config 读取配置
	token = OS.get_environment("GAMEFORGE_PREVIEW_TOKEN").strip_edges()
	if token.is_empty():
		var settings = preload("res://addons/gameforge/settings.gd").new()
		token = str(settings.get_value("screenshot_token", TOKEN_DEFAULT)).strip_edges()

	var port_str := OS.get_environment("GAMEFORGE_PREVIEW_PORT").strip_edges()
	if not port_str.is_empty() and port_str.is_valid_int():
		port = int(port_str)
	else:
		var settings = preload("res://addons/gameforge/settings.gd").new()
		port = int(settings.get_value("screenshot_port", PORT_DEFAULT))

	tcp_server = TCPServer.new()
	var err := tcp_server.listen(port, "127.0.0.1")
	if err != OK:
		push_error("[screenshot_server] listen failed on 127.0.0.1:%d err=%d" % [port, err])
		return
	_running = true
	print("[screenshot_server] listening on 127.0.0.1:%d token_set=%s display=%s" % [port, str(not token.is_empty()), DisplayServer.get_name()])

	# headless 模式下没有真实窗口（DisplayServer=null），跳过窗口操作
	if DisplayServer.get_name() != "headless":
		# 把窗口位置设到屏幕外（20000,20000 多屏下也基本不可能被看到）。
		# 注意：绝不能最小化 —— 最小化会停掉 OpenGL 渲染，截图全黑
		# （窗口尺寸/位置由 supervisor 的 CLI 参数在创建时就设好，这里只是兜底再移一次）
		DisplayServer.window_set_position(Vector2i(20000, 20000))


func _exit_tree() -> void:
	_running = false
	if tcp_server:
		tcp_server.stop()


func _process(_delta: float) -> void:
	if not _running or tcp_server == null:
		return
	if not tcp_server.is_connection_available():
		return
	var conn := tcp_server.take_connection()
	if conn == null:
		return
	_handle(conn)


func _handle(conn: StreamPeerTCP) -> void:
	# 读取整个 HTTP 请求（最多 8KB，超过则截断）
	var raw := ""
	var total_bytes := 0
	const MAX_READ := 8192
	while total_bytes < MAX_READ and conn.get_status() == StreamPeerTCP.STATUS_CONNECTED:
		var avail := conn.get_available_bytes()
		if avail <= 0:
			break
		raw += conn.get_utf8_string(avail)
		total_bytes += avail
		if raw.find("\r\n\r\n") >= 0:
			break

	if raw.is_empty():
		conn.disconnect_from_host()
		return

	var lines := raw.split("\r\n")
	if lines.is_empty():
		conn.disconnect_from_host()
		return
	var request_line := lines[0].split(" ")
	if request_line.size() < 2:
		conn.disconnect_from_host()
		return
	var method := request_line[0]
	var full_path := request_line[1]
	var route_path := full_path.split("?", true, 1)[0] if "?" in full_path else full_path
	var query := full_path.split("?", true, 1)[1] if "?" in full_path else ""

	if method != "GET":
		_send_json(conn, 405, {"error": "Method Not Allowed"})
		return

	if route_path == "/health":
		_send_json(conn, 200, {
			"status": "ok",
			"engine": "godot",
			"version": "%d.%d.%d" % [
				Engine.get_version_info().major,
				Engine.get_version_info().minor,
				Engine.get_version_info().patch,
			],
			"current_frame": current_frame,
		})
		return

	# 其余路由需鉴权
	if not _check_token(raw):
		_send_json(conn, 401, {"error": "Unauthorized"})
		return

	if route_path == "/screenshot":
		var frame := _parse_int(query, "frame", current_frame)
		current_frame = frame
		_advance_to_frame(frame)
		var img := _capture_viewport()
		_send_png(conn, img, frame)
		return

	if route_path == "/frame/advance":
		var frame2 := _parse_int(query, "frame", current_frame)
		current_frame = frame2
		_advance_to_frame(frame2)
		_send_json(conn, 200, {"frame": current_frame})
		return

	_send_json(conn, 404, {"error": "Not Found", "path": route_path})


func _advance_to_frame(target_frame: int) -> void:
	## 推进虚拟时间到目标帧号对应时刻
	## （单次最多推进 600 帧，防止客户端停摆后爆栈）
	var max_step := 600
	var step := target_frame - int(simulated_dt_total * fps_target)
	var safe_step: int = clamp(step, -max_step, max_step)
	simulated_dt_total += float(safe_step) / fps_target
	# 让 Godot 内部渲染管线推进一步（_process 在主循环中已经会跑）


func _capture_viewport() -> Image:
	# 1) 优先：注入的 SubViewport —— 预览窗口只有 64x64，
	#    根视口跟着窗口缩放，只有 SubViewport 保有全分辨率画面
	if sub_viewport != null:
		var tex2 := sub_viewport.get_texture()
		if tex2 != null:
			var img2 := tex2.get_image()
			if img2 != null and not img2.is_empty():
				return img2

	# 2) 备用：主 root viewport（未接 SubViewport 时的兜底）
	var main_vp := get_viewport()
	if main_vp != null:
		var tex := main_vp.get_texture()
		if tex != null:
			var img := tex.get_image()
			if img != null and not img.is_empty():
				return img

	# 3) 兜底：占位图（headless dummy 下总是走这里）
	return _placeholder(640, 360)


func _placeholder(w: int, h: int) -> Image:
	var img := Image.create(w, h, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.05, 0.08, 0.16, 1.0))
	# 顶部条带
	for y in range(0, 8):
		for x in range(0, w):
			img.set_pixel(x, y, Color(0.0, 0.78, 1.0, 0.85))
	# 底部条带
	for y in range(h - 8, h):
		for x in range(0, w):
			img.set_pixel(x, y, Color(0.62, 0.42, 1.0, 0.85))
	# 中心滑动条（位置随 current_frame 变化）
	var phase := float(current_frame % 16) / 16.0
	var cx := int(w * (0.2 + 0.6 * phase))
	var cy := int(h * 0.5)
	for y in range(cy - 4, cy + 4):
		for x in range(cx - 24, cx + 24):
			if x >= 0 and x < w and y >= 0 and y < h:
				img.set_pixel(x, y, Color(0.85, 0.95, 1.0, 0.7))
	return img


func _send_json(conn: StreamPeerTCP, status: int, body: Dictionary) -> void:
	var json := JSON.stringify(body)
	var status_text := "OK" if status == 200 else "Error"
	var resp := "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % [
		status, status_text, json.length(), json
	]
	conn.put_data(resp.to_utf8_buffer())
	conn.disconnect_from_host()


func _send_png(conn: StreamPeerTCP, img: Image, frame: int) -> void:
	# Godot 4 内部 user:// 路径
	var tmp_path := "user://_gf_frame.png"
	var save_err := img.save_png(tmp_path)
	if save_err != OK:
		_send_json(conn, 500, {"error": "save_png failed", "code": int(save_err)})
		return

	var f := FileAccess.open(tmp_path, FileAccess.READ)
	if f == null:
		_send_json(conn, 500, {"error": "cannot reopen png"})
		return
	var bytes := f.get_buffer(f.get_length())
	f.close()

	var header := "HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\nCache-Control: no-store\r\nX-Preview-Frame: %d\r\nConnection: close\r\n\r\n" % [bytes.size(), frame]
	conn.put_data(header.to_utf8_buffer())
	conn.put_data(bytes)
	conn.disconnect_from_host()


func _check_token(raw: String) -> bool:
	if token.is_empty():
		return true
	for line in raw.split("\r\n"):
		var lower := line.to_lower()
		if lower.begins_with("x-api-key:"):
			return line.substr(11).strip_edges() == token
	return false


func _parse_int(query: String, key: String, default_val: int) -> int:
	for kv in query.split("&"):
		if kv.begins_with(key + "="):
			var v := kv.substr(key.length() + 1)
			if v.is_valid_int():
				return int(v)
	return default_val