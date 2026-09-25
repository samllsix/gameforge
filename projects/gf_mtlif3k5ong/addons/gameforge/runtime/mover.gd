extends ColorRect
## 漂浮/摇晃动画（_process 平滑摆动）
@export var move_range_x: float = 60.0
@export var move_range_y: float = 12.0
@export var move_speed: float = 1.5
@export var phase: float = 0.0

var _origin: Vector2

func _ready() -> void:
    _origin = position

func _process(delta: float) -> void:
    var t: float = _t() + phase
    position.x = _origin.x + sin(t * move_speed) * move_range_x
    position.y = _origin.y + cos(t * move_speed * 0.7) * move_range_y

func _t() -> float:
    return float(Time.get_ticks_msec()) / 1000.0
