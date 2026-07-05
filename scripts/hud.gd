## HUD — 游戏界面
extends CanvasLayer

## 分数标签
@onready var score_label: Label = $ScoreLabel


func _ready() -> void:
	# 连接 GameManager 信号
	GameManager.score_changed.connect(_on_score_changed)
	GameManager.lives_changed.connect(_on_lives_changed)
	GameManager.game_over.connect(_on_game_over)


## 更新分数显示
func _on_score_changed(new_score: int) -> void:
	if score_label:
		score_label.text = "分数: %d" % new_score


## 更新生命显示
func _on_lives_changed(new_lives: int) -> void:
	print("剩余生命: ", new_lives)


## 游戏结束
func _on_game_over() -> void:
	print("游戏结束！")
	# 可以在这里显示游戏结束 UI
