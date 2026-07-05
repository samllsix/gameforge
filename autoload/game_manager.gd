## GameManager — 全局游戏管理器
## Autoload 单例，全局可访问
extends Node

## 信号：分数变化
signal score_changed(new_score: int)
## 信号：生命变化
signal lives_changed(new_lives: int)
## 信号：游戏结束
signal game_over()

## 当前分数
var score: int = 0
## 当前生命
var lives: int = 3
## 最大生命
var max_lives: int = 3
## 游戏是否暂停
var is_paused: bool = false


func _ready() -> void:
	print("GameManager 已初始化")


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		toggle_pause()


## 增加分数
func add_score(amount: int) -> void:
	score += amount
	score_changed.emit(score)


## 减少生命
func lose_life() -> void:
	lives -= 1
	lives_changed.emit(lives)
	if lives <= 0:
		_on_game_over()


## 恢复生命
func heal(amount: int = 1) -> void:
	lives = mini(lives + amount, max_lives)
	lives_changed.emit(lives)


## 切换暂停
func toggle_pause() -> void:
	is_paused = !is_paused
	get_tree().paused = is_paused


## 重新开始游戏
func restart_game() -> void:
	score = 0
	lives = max_lives
	score_changed.emit(score)
	lives_changed.emit(lives)
	get_tree().paused = false
	get_tree().reload_current_scene()


## 游戏结束处理
func _on_game_over() -> void:
	game_over.emit()
	print("游戏结束！最终分数: ", score)
