extends Area2D
## 道具：自动漂浮 + 旋转 + 玩家碰到后消失
@export var collected: bool = false

func _on_body_entered(body: Node) -> void:
    if collected: return
    if body.name == "Player" or body.is_in_group("player"):
        collected = true
        var tween := create_tween()
        tween.set_parallel(true)
        tween.tween_property(self, "scale", Vector2(1.8, 1.8), 0.15)
        tween.tween_property(self, "modulate:a", 0.0, 0.2)
        tween.chain().tween_callback(queue_free)
