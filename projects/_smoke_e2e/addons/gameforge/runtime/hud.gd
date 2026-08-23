extends Control
## HUD：计时 + 分数（用玩家位置做伪分）。挂在 HUD/HudRoot 下，Label 兄弟节点为 HUD 直接子。
@export var theme_color: Color = Color.WHITE

var _t0: int = 0
var _score: int = 0

func _ready() -> void:
    _t0 = Time.get_ticks_msec()
    set_process(true)

func _process(_delta: float) -> void:
    var elapsed: int = Time.get_ticks_msec() - _t0
    var sec: int = int(elapsed / 1000)
    var mm: String = "%02d" % int(sec / 60)
    var ss: String = "%02d" % int(sec % 60)
    var hud: Node = get_parent()
    var tl: Label = hud.get_node_or_null("TimeLabel") as Label
    if tl:
        tl.text = mm + ":" + ss
    var sl: Label = hud.get_node_or_null("ScoreLabel") as Label
    if sl:
        sl.text = "SCORE " + str(_score + int(elapsed / 1000) * 7)
