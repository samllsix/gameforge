## GameForge 一次性场景截图脚本
## 通过 `godot --headless --script res://addons/gameforge/screenshot_scene.gd --path <project>`
## 执行。读取项目根目录的 _gf_screenshot_manifest.json，根据 manifest 加载指定场景，
## 渲染若干帧后把当前 Viewport 保存为 PNG 到 _gf_screenshot_output.png，然后退出。
##
## Manifest 格式（写到项目根 _gf_screenshot_manifest.json）：
## {
##   "scene_path": "res://scenes/main.tscn",
##   "output_path": "_gf_screenshot_output.png",
##   "width": 640,
##   "height": 360,
##   "warmup_frames": 30,
##   "frame_index": 0
## }
extends SceneTree


func _initialize() -> void:
	var project_root := ProjectSettings.globalize_path("res://")
	var manifest_path := project_root + "_gf_screenshot_manifest.json"
	var output_path := ""
	var width := 640
	var height := 360
	var warmup_frames := 30
	var frame_index := 0
	var scene_path := "res://main.tscn"

	if not FileAccess.file_exists(manifest_path):
		push_error("[screenshot_scene] manifest missing: " + manifest_path)
		quit(1)
		return

	var f := FileAccess.open(manifest_path, FileAccess.READ)
	if f == null:
		push_error("[screenshot_scene] cannot open manifest")
		quit(1)
		return
	var raw := f.get_as_text()
	f.close()

	var parsed: Variant = JSON.parse_string(raw)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("[screenshot_scene] manifest is not a JSON object")
		quit(1)
		return

	var data: Dictionary = parsed
	scene_path = str(data.get("scene_path", scene_path))
	output_path = str(data.get("output_path", "_gf_screenshot_output.png"))
	width = int(data.get("width", width))
	height = int(data.get("height", height))
	warmup_frames = int(data.get("warmup_frames", warmup_frames))
	frame_index = int(data.get("frame_index", frame_index))

	if not output_path.is_absolute_path():
		output_path = project_root + output_path

	# 加载场景
	var packed: PackedScene = load(scene_path) as PackedScene
	if packed == null:
		push_error("[screenshot_scene] cannot load scene: " + scene_path)
		quit(1)
		return

	var instance: Node = packed.instantiate()
	if instance == null:
		push_error("[screenshot_scene] cannot instantiate scene")
		quit(1)
		return

	# 创建一个独立 SubViewport 来稳定渲染分辨率（与 manifest 一致）
	var sub_viewport := SubViewport.new()
	sub_viewport.size = Vector2i(width, height)
	sub_viewport.transparent_bg = false
	sub_viewport.handle_input_locally = false
	sub_viewport.own_world_3d = (instance is Node3D)
	root.add_child(sub_viewport)
	sub_viewport.add_child(instance)

	# 推进若干帧让 _ready / _process 跑出第一帧画面（_process 动画也用得上）
	for i in range(warmup_frames):
		await create_timer(0.016).timeout
		await process_frame

	# 抓取 viewport 纹理并保存 PNG
	var img: Image = sub_viewport.get_texture().get_image()
	var used_fallback := false
	if img == null or img.is_empty():
		# headless 默认使用 dummy 渲染器，没有真实 framebuffer。
		# 这里退而求其次：用 RenderingServer 渲染一次，或者生成一张描述场景信息的
		# placeholder PNG，确保前端至少能验证"Godot 进程执行成功 + 截图接口正常"。
		used_fallback = true
		img = _build_placeholder_image(width, height, scene_path, frame_index)

	# PNG 默认 RGBA8，输出宽度按 manifest；让浏览器直接 drawImage
	var save_err := img.save_png(output_path)
	if save_err != OK:
		push_error("[screenshot_scene] save_png failed: %d" % save_err)
		quit(1)
		return

	print("[screenshot_scene] OK frame=%d -> %s (fallback=%s)" % [frame_index, output_path, str(used_fallback)])
	quit(0)


func _build_placeholder_image(w: int, h: int, scene_path: String, frame_idx: int) -> Image:
	"""当 headless dummy 渲染器无法提供 viewport texture 时，生成一张说明性占位图。

	这样前端轮询可以确认链路正常（接口通了、Godot 进程跑了），但不会假装是真游戏画面。
	"""
	var img := Image.create(w, h, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.06, 0.08, 0.16, 1.0))
	# 顶部条带
	for y in range(0, 8):
		for x in range(0, w):
			img.set_pixel(x, y, Color(0.0, 0.78, 1.0, 0.85))
	# 底部条带
	for y in range(h - 8, h):
		for x in range(0, w):
			img.set_pixel(x, y, Color(0.62, 0.42, 1.0, 0.85))
	# 中心区域：写几行说明文字（用 PackedByteArray 简单点阵，仅在较大图上画）
	# 这里不在 GDScript 端做字体渲染，避免依赖资源；前端 drawImage 后再叠 DOM 文本。
	# 简单点缀：4 个角发光方块
	for y in range(h / 2 - 6, h / 2 + 6):
		for x in range(8, 28):
			img.set_pixel(x, y, Color(0.0, 0.95, 1.0, 0.6))
	for y in range(h / 2 - 6, h / 2 + 6):
		for x in range(w - 28, w - 8):
			img.set_pixel(x, y, Color(0.7, 0.5, 1.0, 0.6))
	# 在图像中嵌入"frame index"的几何变化（让连续帧看起来不同）
	var phase := float(frame_idx % 16) / 16.0
	var cx := int(w * (0.3 + 0.4 * phase))
	var cy := int(h * 0.5)
	for y in range(cy - 3, cy + 3):
		for x in range(cx - 18, cx + 18):
			if x >= 0 and x < w and y >= 0 and y < h:
				img.set_pixel(x, y, Color(0.85, 0.95, 1.0, 0.7))
	return img
