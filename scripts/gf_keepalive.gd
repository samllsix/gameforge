extends SceneTree

# 保活脚本：在编辑器模式下让主循环持续运行，
# 使 GameForge 插件自动拉起的 8765 HTTP 服务器保持可访问。
# 150s 后自动退出，避免挂起。

func _initialize() -> void:
	var t := Timer.new()
	t.wait_time = 150.0
	t.one_shot = true
	t.timeout.connect(func(): print("[KEEPALIVE] 超时，退出编辑器"); quit())
	get_root().add_child(t)
	t.start()
	print("[KEEPALIVE] 编辑器已保活 150s；插件 HTTP 服务器应在 8765 监听")
