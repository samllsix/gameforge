extends Area2D

signal collected(score_value: int)

@export var score_value: int = 10

func _ready() -> void:
	body_entered.connect(_on_body_entered)

func _on_body_entered(body: Node2D) -> void:
	if body.name == "Player" or body.is_in_group("player"):
		collected.emit(score_value)
		var score_manager = get_tree().current_scene.get_node_or_null("ScoreManager")
		if score_manager and score_manager.has_method("add_score"):
			score_manager.add_score(score_value)
		queue_free()
