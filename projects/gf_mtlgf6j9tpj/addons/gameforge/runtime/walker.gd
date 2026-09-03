extends CharacterBody2D
## 敌人左右巡逻
@export var speed: float = 50.0
@export var range: float = 80.0
@export var color_seed: int = 0

var _origin: float
var _dir: float = 1.0

func _ready() -> void:
    _origin = position.x
    modulate = Color.from_hsv(fmod(float(color_seed) * 0.1, 1.0), 0.8, 1.0)

func _physics_process(delta: float) -> void:
    position.x += speed * _dir * delta
    if position.x > _origin + range:
        _dir = -1.0
    elif position.x < _origin - range:
        _dir = 1.0
    velocity = Vector2(speed * _dir, 0)
