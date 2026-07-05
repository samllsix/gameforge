## Coin — 金币收集物
extends Area2D

## 浮动幅度
@export var float_amplitude: float = 5.0
## 浮动速度
@export var float_speed: float = 2.0

## 初始 Y 位置
var _start_y: float
## 时间计数器
var _time: float = 0.0

## 节点引用
@onready var animated_sprite: AnimatedSprite2D = $AnimatedSprite2D


func _ready() -> void:
	_start_y = position.y
	# 连接碰撞信号
	body_entered.connect(_on_body_entered)


func _process(delta: float) -> void:
	# 浮动动画
	_time += delta
	position.y = _start_y + sin(_time * float_speed) * float_amplitude


## 当有物体进入
func _on_body_entered(body: Node2D) -> void:
	if body.is_in_group("player"):
		if body.has_method("collect_coin"):
			body.collect_coin()
		queue_free()
