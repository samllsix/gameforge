## GameForge 实时预览运行时（autoload 入口）
## 在项目 project.godot 的 [autoload] 段以 GameForgePreviewRunner 名注册，
## Godot 主场景加载时它会第一个被挂上。负责：
## 1) 等待场景加载完毕
## 2) 把 ScreenshotServer 挂上（暴露 :8769 HTTP）
## 3) 把窗口放到屏幕外 (20000,20000)，用户感知不到
##
## 调用方式：
##   godot.exe --rendering-driver opengl3 --audio-driver Dummy \
##             --position 20000,20000 --resolution 640x360 \
##             --path <project>
## （注意：不是 --script 模式，是普通 project 启动方式）
extends Node

const PORT_DEFAULT := 8769
const TOKEN_DEFAULT := "gf_screenshot_local"

var _screenshot_server: Node = null


func _ready() -> void:
	var port := PORT_DEFAULT
	var token := TOKEN_DEFAULT

	var port_env := OS.get_environment("GAMEFORGE_PREVIEW_PORT")
	if not port_env.is_empty() and port_env.is_valid_int():
		port = int(port_env)
	var token_env := OS.get_environment("GAMEFORGE_PREVIEW_TOKEN")
	if not token_env.is_empty():
		token = token_env

	# 创建 ScreenshotServer（Node，自带 TCPServer）
	var SS := preload("res://addons/gameforge/screenshot_server.gd")
	_screenshot_server = SS.new()
	_screenshot_server.name = "GameForgeScreenshotServer"
	_screenshot_server.port = port
	_screenshot_server.token = token
	# 挂在 root（autoload 自带 root 引用）
	get_tree().root.add_child.call_deferred(_screenshot_server)

	# 等一帧让 Godot 创建窗口 + 加载主场景
	call_deferred("_post_init", port, token)


func _post_init(port: int, token: String) -> void:
	# 窗口位置已在启动时通过 --position 命令行参数设好，这里只确认。
	# 不最小化（否则 OpenGL framebuffer 不会被渲染）。
	if DisplayServer.get_name() != "headless":
		pass  # 位置由 --position 决定

	print("[preview_runner] ready scene=%s port=%d display=%s pos=%s size=%s screen=%s" % [
		str(get_tree().current_scene),
		port,
		DisplayServer.get_name(),
		str(DisplayServer.window_get_position()),
		str(DisplayServer.window_get_size()),
		str(DisplayServer.screen_get_size()),
	])