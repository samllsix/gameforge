extends ColorRect
## 持续旋转 + 微微缩放
@export var rot_speed: float = 1.5

func _process(delta: float) -> void:
    rotation += rot_speed * delta
    var s: float = 1.0 + 0.1 * sin(float(Time.get_ticks_msec()) / 300.0)
    scale = Vector2(s, s)
