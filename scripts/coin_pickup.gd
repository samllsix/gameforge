extends Area2D

signal collected(score_value: int)

@export var score_value: int = 10

func _ready() -> void:
    body_entered.connect(_on_body_entered)

func _on_body_entered(body: Node2D) -> void:
    if body.is_in_group("player") or body.name == "Player":
        collected.emit(score_value)
        queue_free()
