extends Area2D
## 道具：自动漂浮 + 旋转 + 玩家碰到后消失并计分
@export var collected: bool = false
@export var score_value: int = 10

func _on_body_entered(body: Node) -> void:
    if collected: return
    if body.name == "Player" or body.is_in_group("player"):
        collected = true
        _sfx("coin")
        var gf := get_tree().get_first_node_in_group("game_flow")
        if gf:
            gf.add_score(score_value)
        var tween := create_tween()
        tween.set_parallel(true)
        tween.tween_property(self, "scale", Vector2(1.8, 1.8), 0.15)
        tween.tween_property(self, "modulate:a", 0.0, 0.2)
        tween.chain().tween_callback(queue_free)

func _sfx(n: String) -> void:
    var stream := load("res://assets/sfx/" + n + ".wav")
    if stream == null:
        return
    var p := AudioStreamPlayer.new()
    p.stream = stream
    add_child(p)
    p.finished.connect(p.queue_free)
    p.play()
