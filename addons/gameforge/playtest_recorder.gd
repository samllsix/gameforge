## GameForge playtest 录制器（autoload 入口）
##
## playtest 验收原则："编译通过/能启动"不算验证，
## 必须真正驱动玩家操作并留下证据。本脚本由 PlaytestRunner 注入到
## 生成项目的 [autoload] 段，以 --headless 启动后：
##   1) 读取声明式动作脚本（绝对路径，环境变量 GAMEFORGE_PLAYTEST_ACTIONS）
##   2) 按时间轴用 Input.parse_input_event 注入真实输入事件
##   3) 每 N 帧用 get_viewport().get_texture().get_image() 抓帧（进程内，
##      headless 可用，无需显示器，固定步长采样时序确定）
##   4) 结束后写 report.json（GAMEFORGE_PLAYTEST_OUT 指向的目录）并退出
##
## 动作脚本格式（JSON 数组，t 为自启动起的秒数）：
##   [{"t": 0.5, "type": "key",    "key": "Right", "pressed": true},
##    {"t": 1.0, "type": "action", "action": "jump", "pressed": true},
##    {"t": 2.0, "type": "mouse_button", "button": "left", "pos": [320,180], "pressed": true},
##    {"t": 2.5, "type": "mouse_motion", "pos": [400,200], "relative": [80,20]}]
extends Node

const REPORT_SCHEMA := "gameforge.playtest_report.v1"
const DEFAULT_FRAME_INTERVAL := 10
const DEFAULT_TAIL := 1.0

var _actions: Array = []
var _out_dir: String = ""
var _start_ms: int = 0
var _next_action_idx: int = 0
var _executed: int = 0
var _frames_captured: int = 0
var _frame_interval: int = DEFAULT_FRAME_INTERVAL
var _duration: float = 0.0
var _finished: bool = false
var _frames_dir: DirAccess = null


func _ready() -> void:
	var actions_path := OS.get_environment("GAMEFORGE_PLAYTEST_ACTIONS")
	_out_dir = OS.get_environment("GAMEFORGE_PLAYTEST_OUT")
	var interval_env := OS.get_environment("GAMEFORGE_PLAYTEST_FRAME_INTERVAL")
	if not interval_env.is_empty() and interval_env.is_valid_int():
		_frame_interval = maxi(1, int(interval_env))

	if actions_path.is_empty() or _out_dir.is_empty():
		# 未配置 playtest（普通启动/预览）：静默自禁用，绝不能退出游戏——
		# 录制器会永久驻留在已试玩过的项目里，用户之后手动打开该项目必须不受影响。
		set_process(false)
		return

	var txt := _read_text(actions_path)
	if txt.is_empty():
		_finish(false, "actions_file_unreadable")
		return

	var parsed = JSON.parse_string(txt)
	if not (parsed is Array):
		_finish(false, "actions_json_not_array")
		return
	_actions = parsed

	# 计算总时长 = 最后一个动作 + tail
	_duration = DEFAULT_TAIL
	for a in _actions:
		if a is Dictionary and a.has("t"):
			_duration = maxf(_duration, float(a["t"]) + DEFAULT_TAIL)

	var frames_path := _out_dir.path_join("frames")
	DirAccess.make_dir_recursive_absolute(frames_path)
	_frames_dir = DirAccess.open(frames_path)
	_start_ms = Time.get_ticks_msec()
	set_process(true)


func _process(_delta: float) -> void:
	if _finished:
		return
	var elapsed := float(Time.get_ticks_msec() - _start_ms) / 1000.0

	# 到点的动作逐个注入
	while _next_action_idx < _actions.size():
		var a = _actions[_next_action_idx]
		if not (a is Dictionary):
			_next_action_idx += 1
			continue
		var t := float(a.get("t", 0.0))
		if t > elapsed:
			break
		_dispatch(a)
		_executed += 1
		_next_action_idx += 1

	# 固定步长抓帧
	var frame := Engine.get_frames_drawn()
	if frame > 0 and frame % _frame_interval == 0:
		_capture_frame(frame)

	if elapsed >= _duration:
		_finish(true, "")


func _dispatch(a: Dictionary) -> void:
	var type := str(a.get("type", ""))
	match type:
		"key":
			var ev := InputEventKey.new()
			ev.physical_keycode = OS.find_keycode_from_string(str(a.get("key", "")))
			ev.keycode = ev.physical_keycode
			ev.pressed = bool(a.get("pressed", true))
			ev.echo = false
			Input.parse_input_event(ev)
		"action":
			var ev2 := InputEventAction.new()
			ev2.action = str(a.get("action", ""))
			ev2.pressed = bool(a.get("pressed", true))
			ev2.strength = 1.0
			Input.parse_input_event(ev2)
		"mouse_button":
			var ev3 := InputEventMouseButton.new()
			ev3.button_index = _mouse_button_index(str(a.get("button", "left")))
			ev3.pressed = bool(a.get("pressed", true))
			var pos := a.get("pos", [0, 0])
			if pos is Array and pos.size() >= 2:
				ev3.position = Vector2(float(pos[0]), float(pos[1]))
			Input.parse_input_event(ev3)
		"mouse_motion":
			var ev4 := InputEventMouseMotion.new()
			var pos2 := a.get("pos", [0, 0])
			if pos2 is Array and pos2.size() >= 2:
				ev4.position = Vector2(float(pos2[0]), float(pos2[1]))
			var rel := a.get("relative", [0, 0])
			if rel is Array and rel.size() >= 2:
				ev4.relative = Vector2(float(rel[0]), float(rel[1]))
			Input.parse_input_event(ev4)


func _mouse_button_index(name: String) -> MouseButton:
	match name:
		"left": return MOUSE_BUTTON_LEFT
		"right": return MOUSE_BUTTON_RIGHT
		"middle": return MOUSE_BUTTON_MIDDLE
		_: return MOUSE_BUTTON_LEFT


func _capture_frame(frame: int) -> void:
	if _frames_dir == null:
		return
	var img := get_viewport().get_texture().get_image()
	if img == null or img.is_empty():
		return
	var err := img.save_png(_out_dir.path_join("frames/f%05d.png" % frame))
	if err == OK:
		_frames_captured += 1


func _finish(ok: bool, reason: String) -> void:
	if _finished:
		return
	_finished = true
	var report := {
		"schema": REPORT_SCHEMA,
		"ok": ok,
		"fail_reason": reason,
		"actions_total": _actions.size(),
		"actions_executed": _executed,
		"frames_captured": _frames_captured,
		"duration_seconds": float(Time.get_ticks_msec() - _start_ms) / 1000.0,
		"frame_interval": _frame_interval,
		"headless": DisplayServer.get_name() == "headless",
		"scene_file_path": get_tree().current_scene.scene_file_path if get_tree().current_scene else "",
	}
	var path := _out_dir.path_join("report.json")
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify(report, "  "))
		f.close()
		print("[playtest_recorder] report written: ", path)
	else:
		print("[playtest_recorder] report write failed: ", FileAccess.get_open_error())
	get_tree().quit(0 if ok else 1)


func _read_text(path: String) -> String:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return ""
	var txt := f.get_as_text()
	f.close()
	return txt
