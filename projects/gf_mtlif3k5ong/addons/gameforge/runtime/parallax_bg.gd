extends Node2D
## 视差层：整体向左滚动 Deco* 子节点，越界后按周期 wrap 回右侧，无缝循环
@export var speed: float = 1.0
@export var wrap_left: float = -704.0
@export var wrap_period: float = 768.0

func _process(delta: float) -> void:
	var dx: float = speed * 30.0 * delta
	for c in get_children():
		if c.name.begins_with("Deco"):
			c.position.x -= dx
			if c.position.x < wrap_left:
				c.position.x += wrap_period
