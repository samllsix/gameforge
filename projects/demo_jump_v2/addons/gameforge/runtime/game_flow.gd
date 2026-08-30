extends CanvasLayer
## 游戏流程中枢：开始画面 / Esc 暂停 / 游戏结束与重开 / 计分。
## 进程模式 ALWAYS：树暂停时仍能响应输入（暂停面板、重开）。
## UI 面板（BootPanel/PausePanel/OverPanel）由 build_scene_tscn 以兄弟结构生成。

var score: int = 0
var _started: bool = false
var _over: bool = false

func _ready() -> void:
    # 开局先挂起：等玩家点击/按键开始，计时也从这里起步
    get_tree().paused = true
    _set_visible("BootPanel", true)
    _set_visible("PausePanel", false)
    _set_visible("OverPanel", false)
    _sync_score()
    # 预览/截图模式：自动开始（否则截图永远停在开始画面，玩家无法点击）
    if OS.get_environment("GAMEFORGE_PREVIEW_AUTOSTART").to_lower().strip_edges() == "1":
        start_game()

func _unhandled_input(event: InputEvent) -> void:
    var key := event as InputEventKey
    if key and key.pressed and not key.echo:
        if not _started and (key.keycode == KEY_SPACE or key.keycode == KEY_ENTER):
            start_game()
        elif _over and key.keycode == KEY_R:
            restart()
        elif _started and not _over and key.keycode == KEY_ESCAPE:
            toggle_pause()
        return
    var mb := event as InputEventMouseButton
    if mb and mb.pressed and not _started:
        start_game()

func start_game() -> void:
    if _started:
        return
    _started = true
    _set_visible("BootPanel", false)
    _sfx("click")
    get_tree().paused = false

func toggle_pause() -> void:
    var paused := not get_tree().paused
    get_tree().paused = paused
    _set_visible("PausePanel", paused)

func add_score(v: int) -> void:
    score += v
    _sync_score()

func on_game_over() -> void:
    if _over:
        return
    _over = true
    get_tree().paused = true
    _sync_score()
    var result: Label = get_node_or_null("OverPanel/Center/Box/ScoreResult") as Label
    if result:
        result.text = "最终得分  %d" % score
    _set_visible("OverPanel", true)

func restart() -> void:
    get_tree().paused = false
    get_tree().reload_current_scene()

func _on_restart_pressed() -> void:
    restart()

func _on_quit_pressed() -> void:
    get_tree().quit()

func _set_visible(panel: String, visible_now: bool) -> void:
    var p := get_node_or_null(panel)
    if p:
        p.visible = visible_now

func _sync_score() -> void:
    var scene := get_tree().current_scene
    if scene == null:
        return
    var sl: Label = scene.get_node_or_null("HUD/ScoreLabel") as Label
    if sl:
        sl.text = "SCORE " + str(score)

func _sfx(n: String) -> void:
    var stream := load("res://assets/sfx/" + n + ".wav")
    if stream == null:
        return
    var p := AudioStreamPlayer.new()
    p.stream = stream
    add_child(p)
    p.finished.connect(p.queue_free)
    p.play()
