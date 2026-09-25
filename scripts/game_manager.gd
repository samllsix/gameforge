## 游戏管理器 — 全局计分与状态（Autoload 单例）
extends Node

# ==================== 信号 ====================
signal score_changed(new_score: int)
signal health_changed(new_health: int)
signal game_over()
signal game_won()

# ==================== 私有变量 ====================
var _score: int = 0
var _health: int = 3
var _coins: int = 0
var _total_coins: int = 5

func add_score(amount: int) -> void:
    _score += amount
    score_changed.emit(_score)

func collect_coin() -> void:
    _coins += 1
    add_score(1)
    if _coins >= _total_coins:
        game_won.emit()

func take_damage(amount: int = 1) -> void:
    _health -= amount
    health_changed.emit(_health)
    if _health <= 0:
        game_over.emit()
