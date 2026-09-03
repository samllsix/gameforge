extends Control
## HUD：计时显示（分数由 GameFlow 节点统一管理，避免两处写同一 Label）
@export var theme_color: Color = Color.WHITE

var _t0: int = 0

func _ready() -> void:
    _t0 = Time.get_ticks_msec()
    set_process(true)

func _process(_delta: float) -> void:
    if get_tree().paused:
        return
    var elapsed: int = Time.get_ticks_msec() - _t0
    var sec: int = int(elapsed / 1000)
    var mm: String = "%02d" % int(sec / 60)
    var ss: String = "%02d" % int(sec % 60)
    var hud: Node = get_parent()
    var tl: Label = hud.get_node_or_null("TimeLabel") as Label
    if tl:
        tl.text = mm + ":" + ss
