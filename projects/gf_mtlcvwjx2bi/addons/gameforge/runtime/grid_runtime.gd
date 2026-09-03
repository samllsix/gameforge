extends Node2D
## GameForge 网格游戏确定性运行时 —— 贪吃蛇 / Pong / 2048 / 打砖块 / 推箱子 / 扫雷
## 一个脚本覆盖六个品类：LLM 只决定 mode 参数，玩法零生成、零修改。
## 胜负通过 game_flow（组 "game_flow"）上报；分数增量上报。

@export var mode: String = "snake"

const COL_SCORE_COIN := Color(1.0, 0.85, 0.3)
const COL_FG := Color(0.92, 0.95, 1.0)
const COL_BG_DIM := Color(0.08, 0.1, 0.16, 0.85)
const COL_ACCENT := Color(0.4, 0.85, 1.0)
const COL_DANGER := Color(1.0, 0.4, 0.4)

var _gf: Node = null
var _sent_score: int = 0
var _finished: bool = false

# ── 通用小工具 ────────────────────────────────────────────────

func _ready() -> void:
	_gf = get_tree().get_first_node_in_group("game_flow")
	randomize()
	match mode:
		"snake": _init_snake()
		"pong": _init_pong()
		"merge_2048": _init_2048()
		"breakout": _init_breakout()
		"sokoban": _init_sokoban()
		"minesweeper": _init_mines()
		_: _init_snake()

func _process(delta: float) -> void:
	if _finished:
		return
	match mode:
		"snake": _tick_snake(delta)
		"pong": _tick_pong(delta)
		"breakout": _tick_breakout(delta)
	queue_redraw()

func _draw() -> void:
	match mode:
		"snake": _draw_snake()
		"pong": _draw_pong()
		"merge_2048": _draw_2048()
		"breakout": _draw_breakout()
		"sokoban": _draw_sokoban()
		"minesweeper": _draw_mines()

func _unhandled_input(event: InputEvent) -> void:
	if _finished:
		return
	var key := event as InputEventKey
	if key and key.pressed and not key.echo:
		match mode:
			"snake": _key_snake(key.keycode)
			"merge_2048": _key_2048(key.keycode)
			"breakout": _key_breakout(key.keycode)
			"sokoban": _key_sokoban(key.keycode)
			"minesweeper": _key_mines(key.keycode)
			"pong": _key_pong(key.keycode)
		return
	var mb := event as InputEventMouseButton
	if mb and mb.pressed and mode == "minesweeper":
		_click_mines(mb)

func _board_origin() -> Vector2:
	var vp := get_viewport_rect().size
	return Vector2((vp.x - _board_w()) / 2.0, (vp.y - _board_h()) / 2.0)

func _award(points: int) -> void:
	if points <= 0:
		return
	_sent_score += points
	if _gf:
		_gf.add_score(points)

func _end(win: bool) -> void:
	if _finished:
		return
	_finished = true
	if _gf:
		_gf.on_game_over(win)

func _rect(cell: Vector2i, size: float, origin: Vector2, color: Color) -> void:
	draw_rect(Rect2(origin + Vector2(cell.x * size, cell.y * size), Vector2(size, size)), color)

func _label_center(pos: Vector2, size: Vector2, text: String, font_size: int, color: Color) -> void:
	var font := ThemeDB.fallback_font
	draw_string(font, pos + Vector2(0, size.y / 2 + font_size / 2.0), text,
		HORIZONTAL_ALIGNMENT_CENTER, size.x, font_size, color)

# ═════════════════════ 贪吃蛇 ═════════════════════

const S_COLS := 24
const S_ROWS := 14
const S_CELL := 12.0
var s_snake: Array[Vector2i] = []
var s_dir: Vector2i = Vector2i(1, 0)
var s_pending_dir: Vector2i = Vector2i(1, 0)
var s_food: Vector2i = Vector2i(10, 7)
var s_timer: float = 0.0
var s_step: float = 0.14

func _board_w() -> float:
	return S_COLS * S_CELL if mode == "snake" else 320.0

func _board_h() -> float:
	return S_ROWS * S_CELL if mode == "snake" else 180.0

func _init_snake() -> void:
	s_snake = [Vector2i(6, 7), Vector2i(5, 7), Vector2i(4, 7)]
	s_food = _rand_free_cell()

func _rand_free_cell() -> Vector2i:
	var c := Vector2i(randi() % S_COLS, randi() % S_ROWS)
	while s_snake.has(c):
		c = Vector2i(randi() % S_COLS, randi() % S_ROWS)
	return c

func _key_snake(code: int) -> void:
	var d := s_dir
	if code == KEY_UP or code == KEY_W: d = Vector2i(0, -1)
	elif code == KEY_DOWN or code == KEY_S: d = Vector2i(0, 1)
	elif code == KEY_LEFT or code == KEY_A: d = Vector2i(-1, 0)
	elif code == KEY_RIGHT or code == KEY_D: d = Vector2i(1, 0)
	if d != -s_dir:
		s_pending_dir = d

func _tick_snake(delta: float) -> void:
	s_timer += delta
	if s_timer < s_step:
		return
	s_timer = 0.0
	s_dir = s_pending_dir
	var head: Vector2i = s_snake[0] + s_dir
	if head.x < 0 or head.y < 0 or head.x >= S_COLS or head.y >= S_ROWS or s_snake.has(head):
		_end(false)
		return
	s_snake.push_front(head)
	if head == s_food:
		_award(10)
		s_step = max(0.06, s_step - 0.002)  # 难度递增：越吃越快
		s_food = _rand_free_cell()
	else:
		s_snake.pop_back()

func _draw_snake() -> void:
	var o := _board_origin()
	draw_rect(Rect2(o, Vector2(_board_w(), _board_h())), COL_BG_DIM)
	_rect(s_food, S_CELL, o, COL_SCORE_COIN)
	for i in s_snake.size():
		_rect(s_snake[i], S_CELL - 1.0, o, COL_ACCENT if i == 0 else Color(0.55, 0.75, 0.9))

# ═════════════════════ Pong ═════════════════════

const P_TARGET := 7
const P_PAD_H := 44.0
const P_PAD_W := 6.0
const P_BALL_R := 4.0
var p_me: float = 90.0      # 我方挡板 y（中心）
var p_ai: float = 90.0
var p_ball: Vector2 = Vector2(160, 90)
var p_ball_v: Vector2 = Vector2(130, 70)
var p_my_score: int = 0
var p_ai_score: int = 0

func _init_pong() -> void:
	pass

func _field() -> Rect2:
	var vp := get_viewport_rect().size
	return Rect2(8, 8, vp.x - 16, vp.y - 16)

func _tick_pong(delta: float) -> void:
	var f := _field()
	# 我方挡板：方向键 / W S
	var dir := Input.get_axis("ui_up", "ui_down")
	p_me = clamp(p_me + dir * 220.0 * delta, f.position.y + P_PAD_H / 2, f.end.y - P_PAD_H / 2)
	# AI 挡板：限速跟踪
	var ai_target := p_ball.y
	p_ai = move_toward(p_ai, ai_target, 150.0 * delta)
	p_ai = clamp(p_ai, f.position.y + P_PAD_H / 2, f.end.y - P_PAD_H / 2)
	# 球
	p_ball += p_ball_v * delta
	if p_ball.y - P_BALL_R < f.position.y or p_ball.y + P_BALL_R > f.end.y:
		p_ball_v.y = -p_ball_v.y
		p_ball.y = clamp(p_ball.y, f.position.y + P_BALL_R, f.end.y - P_BALL_R)
	# 挡板碰撞（左我右 AI）
	if p_ball_v.x < 0 and p_ball.x - P_BALL_R < f.position.x + P_PAD_W + 4 			and p_ball.x > f.position.x and abs(p_ball.y - p_me) < P_PAD_H / 2 + P_BALL_R:
		p_ball_v.x = -p_ball_v.x * 1.03
		p_ball_v.y += (p_ball.y - p_me) * 4.0
	if p_ball_v.x > 0 and p_ball.x + P_BALL_R > f.end.x - P_PAD_W - 4 			and p_ball.x < f.end.x and abs(p_ball.y - p_ai) < P_PAD_H / 2 + P_BALL_R:
		p_ball_v.x = -p_ball_v.x * 1.03
		p_ball_v.y += (p_ball.y - p_ai) * 4.0
	# 得分
	if p_ball.x < f.position.x:
		p_ai_score += 1
		_reset_ball()
		if p_ai_score >= P_TARGET: _end(false)
	elif p_ball.x > f.end.x:
		p_my_score += 1
		_award(25)
		_reset_ball()
		if p_my_score >= P_TARGET: _end(true)

func _reset_ball() -> void:
	var f := _field()
	p_ball = f.size / 2.0 + f.position
	p_ball_v = Vector2(130 if randf() > 0.5 else -130, randf_range(-80, 80))

func _key_pong(_code: int) -> void:
	pass  # 移动走 Input 轴，这里留空

func _draw_pong() -> void:
	var f := _field()
	draw_rect(f, COL_BG_DIM)
	draw_rect(Rect2(f.position.x + 4, p_me - P_PAD_H / 2, P_PAD_W, P_PAD_H), COL_ACCENT)
	draw_rect(Rect2(f.end.x - 4 - P_PAD_W, p_ai - P_PAD_H / 2, P_PAD_W, P_PAD_H), COL_DANGER)
	draw_circle(p_ball, P_BALL_R, COL_FG)
	_label_center(f.position + Vector2(0, 6), Vector2(f.size.x / 2, 24), str(p_my_score), 18, COL_FG)
	_label_center(f.position + Vector2(f.size.x / 2, 6), Vector2(f.size.x / 2, 24), str(p_ai_score), 18, COL_FG)

# ═════════════════════ 2048 ═════════════════════

const M_N := 4
const M_CELL := 40.0
var m_grid: Array = []   # Array[Array[int]]

func _init_2048() -> void:
	m_grid = []
	for r in M_N:
		m_grid.append([0, 0, 0, 0])
	_spawn_2048()
	_spawn_2048()

func _spawn_2048() -> void:
	var free: Array[Vector2i] = []
	for r in M_N:
		for c in M_N:
			if m_grid[r][c] == 0:
				free.append(Vector2i(c, r))
	if free.is_empty():
		return
	var pick: Vector2i = free[randi() % free.size()]
	m_grid[pick.y][pick.x] = 2 if randf() < 0.9 else 4

func _slide_row_left(row: Array) -> Array:
	var vals := []
	for v in row:
		if v != 0:
			vals.append(v)
	var out := []
	var i := 0
	while i < vals.size():
		if i + 1 < vals.size() and vals[i] == vals[i + 1]:
			out.append(vals[i] * 2)
			_award(vals[i] * 2)
			i += 2
		else:
			out.append(vals[i])
			i += 1
	while out.size() < M_N:
		out.append(0)
	return out

func _move_2048(dir: Vector2i) -> void:
	var moved := false
	for i in M_N:
		var line := []
		for j in M_N:
			if dir == Vector2i(0, -1) or dir == Vector2i(0, 1):
				# 列方向：取第 i 列（上=正序，下=逆序）
				line.append(m_grid[j if dir == Vector2i(0, -1) else M_N - 1 - j][i])
			else:
				# 行方向：取第 i 行（左=正序，右=逆序）
				line.append(m_grid[i][j if dir == Vector2i(-1, 0) else M_N - 1 - j])
		var before := str(line)
		var slid := _slide_row_left(line)
		if str(slid) != before:
			moved = true
		for j in M_N:
			if dir == Vector2i(0, -1) or dir == Vector2i(0, 1):
				m_grid[j if dir == Vector2i(0, -1) else M_N - 1 - j][i] = slid[j]
			else:
				m_grid[i][j if dir == Vector2i(-1, 0) else M_N - 1 - j] = slid[j]
	if moved:
		_spawn_2048()
		if _has_2048():
			_end(true)
		elif not _can_move():
			_end(false)

func _has_2048() -> bool:
	for r in M_N:
		for c in M_N:
			if m_grid[r][c] >= 2048:
				return true
	return false

func _can_move() -> bool:
	for r in M_N:
		for c in M_N:
			if m_grid[r][c] == 0:
				return true
			if c + 1 < M_N and m_grid[r][c] == m_grid[r][c + 1]:
				return true
			if r + 1 < M_N and m_grid[r][c] == m_grid[r + 1][c]:
				return true
	return false

func _key_2048(code: int) -> void:
	if code == KEY_UP or code == KEY_W: _move_2048(Vector2i(0, -1))
	elif code == KEY_DOWN or code == KEY_S: _move_2048(Vector2i(0, 1))
	elif code == KEY_LEFT or code == KEY_A: _move_2048(Vector2i(-1, 0))
	elif code == KEY_RIGHT or code == KEY_D: _move_2048(Vector2i(1, 0))

func _draw_2048() -> void:
	var o := _board_origin()
	var board_px := M_N * M_CELL
	draw_rect(Rect2(o, Vector2(board_px, board_px)), COL_BG_DIM)
	var colors := {2: Color(0.85, 0.82, 0.75), 4: Color(0.85, 0.75, 0.6),
		8: Color(0.9, 0.65, 0.4), 16: Color(0.9, 0.55, 0.35), 32: Color(0.92, 0.45, 0.3),
		64: Color(0.95, 0.35, 0.25), 128: Color(0.9, 0.8, 0.3), 256: Color(0.9, 0.78, 0.2),
		512: Color(0.9, 0.75, 0.1), 1024: Color(0.85, 0.7, 0.05), 2048: Color(1.0, 0.85, 0.0)}
	for r in M_N:
		for c in M_N:
			var v: int = m_grid[r][c]
			var cell_origin := o + Vector2(c * M_CELL + 2, r * M_CELL + 2)
			draw_rect(Rect2(cell_origin, Vector2(M_CELL - 4, M_CELL - 4)),
				colors.get(v, Color(0.3, 0.3, 0.35)) if v > 0 else Color(0.15, 0.17, 0.22))
			if v > 0:
				var font := ThemeDB.fallback_font
				var text := str(v)
				var fs := 16 if v < 1024 else 13
				draw_string(font, cell_origin + Vector2(0, M_CELL / 2 + fs / 2.0), text,
					HORIZONTAL_ALIGNMENT_CENTER, M_CELL - 4, fs, Color(0.1, 0.1, 0.12))

# ═════════════════════ 打砖块 ═════════════════════

const B_COLS := 8
const B_ROWS := 5
const B_CELL := 30.0
const B_CELL_H := 12.0
var b_bricks: Array[Vector2i] = []
var b_paddle_x: float = 160.0
var b_ball: Vector2 = Vector2(160, 120)
var b_ball_v: Vector2 = Vector2(110, -140)
var b_lives: int = 3

func _init_breakout() -> void:
	for r in B_ROWS:
		for c in B_COLS:
			b_bricks.append(Vector2i(c, r))

func _bricks_left() -> int:
	return b_bricks.size()

func _tick_breakout(delta: float) -> void:
	var vp := get_viewport_rect().size
	var dir := Input.get_axis("ui_left", "ui_right")
	b_paddle_x = clamp(b_paddle_x + dir * 260.0 * delta, 30.0, vp.x - 30.0)
	b_ball += b_ball_v * delta
	if b_ball.x < 6 or b_ball.x > vp.x - 6:
		b_ball_v.x = -b_ball_v.x
		b_ball.x = clamp(b_ball.x, 6, vp.x - 6)
	if b_ball.y < 6:
		b_ball_v.y = -b_ball_v.y
		b_ball.y = 6
	# 挡板
	var paddle_y := vp.y - 14.0
	if b_ball_v.y > 0 and b_ball.y + P_BALL_R > paddle_y and b_ball.y < paddle_y + 8 			and abs(b_ball.x - b_paddle_x) < 32:
		b_ball_v.y = -abs(b_ball_v.y)
		b_ball_v.x += (b_ball.x - b_paddle_x) * 3.0
	# 砖块碰撞（简单网格映射）
	var origin := _breakout_origin()
	var gx := int((b_ball.x - origin.x) / B_CELL)
	var gy := int((b_ball.y - origin.y) / B_CELL_H)
	var cell := Vector2i(gx, gy)
	if b_bricks.has(cell):
		b_bricks.erase(cell)
		b_ball_v.y = -b_ball_v.y
		_award(15)
		if _bricks_left() == 0:
			_end(true)
			return
	# 掉落
	if b_ball.y > vp.y + 10:
		b_lives -= 1
		if b_lives <= 0:
			_end(false)
		else:
			b_ball = Vector2(vp.x / 2, vp.y / 2)
			b_ball_v = Vector2(110, -140)

func _breakout_origin() -> Vector2:
	var vp := get_viewport_rect().size
	return Vector2((vp.x - B_COLS * B_CELL) / 2.0, 24)

func _key_breakout(code: int) -> void:
	if code == KEY_R and b_lives < 3:
		pass  # 保留：无中途重开，走 game_flow

func _draw_breakout() -> void:
	var vp := get_viewport_rect().size
	var origin := _breakout_origin()
	var row_colors := [Color(0.95, 0.5, 0.5), Color(0.95, 0.75, 0.4), Color(0.95, 0.92, 0.5),
		Color(0.5, 0.9, 0.55), Color(0.5, 0.7, 0.95)]
	for brick in b_bricks:
		draw_rect(Rect2(origin + Vector2(brick.x * B_CELL + 1, brick.y * B_CELL_H + 1),
			Vector2(B_CELL - 2, B_CELL_H - 2)), row_colors[brick.y % row_colors.size()])
	draw_rect(Rect2(Vector2(b_paddle_x - 32, vp.y - 14), Vector2(64, 8)), COL_ACCENT)
	draw_circle(b_ball, P_BALL_R, COL_FG)
	_label_center(Vector2(vp.x - 90, 4), Vector2(86, 18), "生命 x%d" % b_lives, 13, COL_FG)

# ═════════════════════ 推箱子 ═════════════════════

const K_MAP := [
	"##########",
	"#........#",
	"#.o.$....#",
	"#...@....#",
	"#.o.$....#",
	"#........#",
	"##########",
]
var k_state: Array[String] = []
var k_player: Vector2i = Vector2i(4, 3)

func _init_sokoban() -> void:
	_reset_sokoban()

func _reset_sokoban() -> void:
	k_state.clear()
	for line in K_MAP:
		k_state.append(line)
	_locate_player()

func _locate_player() -> void:
	for y in k_state.size():
		var x := k_state[y].find("@")
		if x >= 0:
			k_player = Vector2i(x, y)
			return

func _tile(pos: Vector2i) -> String:
	if pos.y < 0 or pos.y >= k_state.size() or pos.x < 0 or pos.x >= k_state[pos.y].length():
		return "#"
	return k_state[pos.y][pos.x]

func _set_tile(pos: Vector2i, ch: String) -> void:
	k_state[pos.y] = k_state[pos.y].substr(0, pos.x) + ch + k_state[pos.y].substr(pos.x + 1)

func _try_push(dir: Vector2i) -> void:
	var target := k_player + dir
	var t := _tile(target)
	if t == "#":
		return
	if t == "$" or t == "*":
		var beyond := target + dir
		var b := _tile(beyond)
		if b == "#" or b == "$" or b == "*":
			return
		# 推箱子（目标点上为 * ，空地为 $）
		_set_tile(beyond, "*" if b == "o" else "$")
		_set_tile(target, "@" if t == "$" else "o")
	if t == "." or t == "o" or t == "$" or t == "*":
		_set_tile(k_player, "." if _tile(k_player) == "@" else "o")
		k_player = target
		_set_tile(k_player, "@")
	_check_sokoban_win()

func _check_sokoban_win() -> void:
	# 所有 $ 都变成 *（箱子在目标点）即胜利
	for y in k_state.size():
		if k_state[y].contains("$"):
			return
	_award(100)
	_end(true)

func _key_sokoban(code: int) -> void:
	var d := Vector2i.ZERO
	if code == KEY_UP or code == KEY_W: d = Vector2i(0, -1)
	elif code == KEY_DOWN or code == KEY_S: d = Vector2i(0, 1)
	elif code == KEY_LEFT or code == KEY_A: d = Vector2i(-1, 0)
	elif code == KEY_RIGHT or code == KEY_D: d = Vector2i(1, 0)
	elif code == KEY_R:
		_reset_sokoban()
		return
	if d != Vector2i.ZERO:
		_try_push(d)

func _draw_sokoban() -> void:
	var o := _board_origin()
	var cs := 16.0
	var board := Vector2(k_state[0].length() * cs, k_state.size() * cs)
	draw_rect(Rect2(o - Vector2(4, 4), board + Vector2(8, 8)), COL_BG_DIM)
	for y in k_state.size():
		for x in k_state[y].length():
			var ch := k_state[y][x]
			var cell := o + Vector2(x * cs, y * cs)
			match ch:
				"#": draw_rect(Rect2(cell, Vector2(cs, cs)), Color(0.35, 0.38, 0.45))
				"o": draw_rect(Rect2(cell + Vector2(4, 4), Vector2(cs - 8, cs - 8)), Color(0.3, 0.7, 0.4))
				"$": draw_rect(Rect2(cell + Vector2(2, 2), Vector2(cs - 4, cs - 4)), Color(0.85, 0.65, 0.3))
				"*": draw_rect(Rect2(cell + Vector2(2, 2), Vector2(cs - 4, cs - 4)), Color(0.4, 0.9, 0.4))
				"@": draw_rect(Rect2(cell + Vector2(2, 2), Vector2(cs - 4, cs - 4)), COL_ACCENT)

# ═════════════════════ 扫雷 ═════════════════════

const MS_COLS := 14
const MS_ROWS := 10
const MS_MINES := 18
const MS_CELL := 16.0
var ms_mines: Array[Vector2i] = []
var ms_revealed: Array[Vector2i] = []
var ms_flags: Array[Vector2i] = []
var ms_started: bool = false

func _init_mines() -> void:
	pass

func _place_mines(safe: Vector2i) -> void:
	ms_mines.clear()
	while ms_mines.size() < MS_MINES:
		var c := Vector2i(randi() % MS_COLS, randi() % MS_ROWS)
		if not ms_mines.has(c) and abs(c.x - safe.x) + abs(c.y - safe.y) > 2:
			ms_mines.append(c)
	ms_started = true

func _neighbors(c: Vector2i) -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	for dy in range(-1, 2):
		for dx in range(-1, 2):
			if dx == 0 and dy == 0:
				continue
			var n := c + Vector2i(dx, dy)
			if n.x >= 0 and n.y >= 0 and n.x < MS_COLS and n.y < MS_ROWS:
				out.append(n)
	return out

func _count_mines(c: Vector2i) -> int:
	var n := 0
	for nb in _neighbors(c):
		if ms_mines.has(nb):
			n += 1
	return n

func _reveal(c: Vector2i) -> void:
	if ms_revealed.has(c) or ms_flags.has(c):
		return
	ms_revealed.append(c)
	if _count_mines(c) == 0 and not ms_mines.has(c):
		for nb in _neighbors(c):
			_reveal(nb)

func _click_mines(mb: InputEventMouseButton) -> void:
	var o := _board_origin()
	var local := (mb.position - o) / MS_CELL
	var cell := Vector2i(int(local.x), int(local.y))
	if cell.x < 0 or cell.y < 0 or cell.x >= MS_COLS or cell.y >= MS_ROWS:
		return
	if not ms_started:
		_place_mines(cell)
	if mb.button_index == MOUSE_BUTTON_RIGHT:
		if ms_flags.has(cell):
			ms_flags.erase(cell)
		elif not ms_revealed.has(cell):
			ms_flags.append(cell)
		return
	if ms_mines.has(cell):
		_end(false)
		return
	_reveal(cell)
	_award(5)
	# 翻开所有非雷格 → 胜利
	if ms_revealed.size() >= MS_COLS * MS_ROWS - MS_MINES:
		_award(100)
		_end(true)

func _key_mines(code: int) -> void:
	if code == KEY_R:
		ms_mines.clear()
		ms_revealed.clear()
		ms_flags.clear()
		ms_started = false

func _draw_mines() -> void:
	var o := _board_origin()
	draw_rect(Rect2(o, Vector2(MS_COLS * MS_CELL, MS_ROWS * MS_CELL)), COL_BG_DIM)
	for y in MS_ROWS:
		for x in MS_COLS:
			var c := Vector2i(x, y)
			var cell := o + Vector2(x * MS_CELL, y * MS_CELL)
			var revealed := ms_revealed.has(c)
			draw_rect(Rect2(cell + Vector2(1, 1), Vector2(MS_CELL - 2, MS_CELL - 2)),
				Color(0.2, 0.23, 0.3) if revealed else Color(0.45, 0.5, 0.6))
			if revealed and not ms_mines.has(c):
				var n := _count_mines(c)
				if n > 0:
					var font := ThemeDB.fallback_font
					draw_string(font, cell + Vector2(0, MS_CELL - 4), str(n),
						HORIZONTAL_ALIGNMENT_CENTER, MS_CELL - 2, 11, COL_ACCENT)
			if ms_flags.has(c):
				_label_center(cell, Vector2(MS_CELL, MS_CELL), "F", 11, COL_SCORE_COIN)
			if ms_mines.has(c) and _finished:
				draw_circle(cell + Vector2(MS_CELL / 2, MS_CELL / 2), 4, COL_DANGER)
