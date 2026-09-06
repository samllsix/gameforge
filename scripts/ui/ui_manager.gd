## HUD 管理器
extends CanvasLayer

@onready var _score_label: Label = $ScoreLabel
@onready var _health_label: Label = $HealthLabel

func _ready() -> void:
    var gm := get_tree().root.get_node_or_null("GameManager")
    if gm:
        if gm.has_signal("score_changed"):
            gm.score_changed.connect(_on_score_changed)
        if gm.has_signal("health_changed"):
            gm.health_changed.connect(_on_health_changed)

func _on_score_changed(new_score: int) -> void:
    if _score_label:
        _score_label.text = "Score: %d" % new_score

func _on_health_changed(new_health: int) -> void:
    if _health_label:
        _health_label.text = "HP: %d" % new_health
