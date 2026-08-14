@tool
extends SceneTree

# GameForge headless 语法校验脚本
# 由 Python 端 GodotEditor.check_scripts() 调用，用于在不打开编辑器 GUI 的情况下
# 校验 AI 生成的 GDScript 是否有解析/语法错误。
#
# 流程：
#   1) Python 写入 res://_gf_check_manifest.json = {"scripts": ["res://scripts/foo.gd", ...]}
#   2) 运行：godot --headless --script res://addons/gameforge/syntax_check.gd --path <项目>
#   3) 本脚本逐个 ResourceLoader.load() 目标脚本，触发 Godot 解析；
#      解析错误会以 "SCRIPT ERROR: ..." / "ERROR: Failed to load script ..." 输出到 stderr
#   4) 写入 res://_gf_check_result.json = {"checked": N} 并退出
#
# 注意：本脚本只加载指定脚本，不会运行项目主场景，因此不受项目已有运行时 bug 影响。

func _initialize() -> void:
	var scripts: Array = []
	var manifest_path := "res://_gf_check_manifest.json"

	if FileAccess.file_exists(manifest_path):
		var f := FileAccess.open(manifest_path, FileAccess.READ)
		if f != null:
			var text := f.get_as_text()
			f.close()
			var json := JSON.new()
			if json.parse(text) == OK:
				var data = json.data
				if data is Dictionary and data.has("scripts"):
					scripts = data["scripts"]

	for p in scripts:
		var path := str(p)
		# CACHE_MODE_IGNORE 强制重新解析，确保拿到最新写入的内容
		var res = ResourceLoader.load(path, "Script", ResourceLoader.CACHE_MODE_IGNORE)
		if res == null:
			printerr("GFCHECK_FAIL: " + path)
		else:
			print("GFCHECK_OK: " + path)

	var result := {"checked": scripts.size()}
	var rf := FileAccess.open("res://_gf_check_result.json", FileAccess.WRITE)
	if rf != null:
		rf.store_string(JSON.stringify(result))
		rf.close()

	quit()
