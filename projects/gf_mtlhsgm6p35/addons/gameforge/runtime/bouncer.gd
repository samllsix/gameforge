extends ColorRect
## 上下小弹跳
@export var bounce_height: float = 6.0
@export var bounce_speed: float = 2.5

var _origin: float

func _ready() -> void:
    _origin = position.y

func _process(delta: float) -> void:
    var t: float = float(Time.get_ticks_msec()) / 1000.0
    position.y = _origin + abs(sin(t * bounce_speed)) * -bounce_height
