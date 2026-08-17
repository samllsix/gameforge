# Coin — 金币收集物
extends Area2D

@export var float_amplitude: float = 5.0
@export var float_speed: float = 2.0

var _start_y: float
var _time: float = 0.0

@onready var mesh: MeshInstance2D = get_node_or_null("Mesh")


func _ready() -> void:
	_start_y = position.y
	body_entered.connect(_on_body_entered)


func _process(delta: float) -> void:
	_time += delta
	position.y = _start_y + sin(_time * float_speed) * float_amplitude


func _on_body_entered(body: Node2D) -> void:
	if body.name == "Player" or body.is_in_group("player"):
		var score_manager = get_tree().current_scene.get_node_or_null("ScoreManager")
		if score_manager and score_manager.has_method("add_score"):
			score_manager.add_score(10)
		elif body.has_method("collect_coin"):
			body.collect_coin()
		queue_free()
