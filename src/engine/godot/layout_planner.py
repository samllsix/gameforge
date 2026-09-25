"""GameForge - 智能关卡布局规划器。

根据角色手感参数（FeelProfile）推导可达跳跃包络，再放置地面、平台、
出生点、敌人、拾取物与装饰，避免固定斜列公式导致的"跳不过去 / 重叠"。

坐标系与 scene_to_godot 一致：
- 场景根节点已居中偏移 (width//2, height//4)；
- 平台/角色使用以根为原点的局部坐标；
- 地面贴在 y ≈ +height/2，角色站在地面上方。

参考 GameFactory-3A 的确定性铺放思路（可复现、可参数化），
补上 3A 示例里没有的「跳跃高度→最大水平位移」约束。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import math
import random

# 与 scene_to_godot.GRID_GENRES 保持同步（避免循环导入，此处本地声明）
GRID_GENRES = frozenset({"snake", "pong", "merge_2048", "breakout", "sokoban", "minesweeper"})


@dataclass
class FeelProfile:
    """统一手感参数 — 同时驱动角色脚本默认值与布局可达性计算。"""

    speed: float = 180.0
    jump_velocity: float = -420.0  # Godot 里向上为负
    gravity: float = 980.0
    coyote_time: float = 0.10
    jump_buffer: float = 0.10
    fall_limit: float = 480.0
    # 布局安全裕度：允许用到最大跳远/跳高的比例（<1 更保守）
    reach_x_ratio: float = 0.72
    reach_y_ratio: float = 0.65

    @property
    def jump_speed(self) -> float:
        return abs(float(self.jump_velocity))

    @property
    def max_jump_height(self) -> float:
        """最大跳高（像素），h = v² / (2g)。"""
        g = max(float(self.gravity), 1e-3)
        return (self.jump_speed ** 2) / (2.0 * g)

    @property
    def time_to_apex(self) -> float:
        g = max(float(self.gravity), 1e-3)
        return self.jump_speed / g

    @property
    def max_same_level_reach(self) -> float:
        """同层最大水平跳远 ≈ speed * (2 * t_apex)。"""
        return float(self.speed) * 2.0 * self.time_to_apex

    def horizontal_reach(self, rise: float) -> float:
        """从当前层向上跳 `rise`（正数=更高）后仍能到达的最大水平距离。

        二次方程：0.5 g t² - v t + rise = 0；
        rise ≥ max_jump_height 时返回 0（不可达）。
        """
        rise = max(0.0, float(rise))
        h_max = self.max_jump_height
        if rise >= h_max * 0.98:
            return 0.0
        g = max(float(self.gravity), 1e-3)
        v = self.jump_speed
        disc = v * v - 2.0 * g * rise
        if disc < 0:
            return 0.0
        # 取落地（较长）根
        t_land = (v + disc ** 0.5) / g
        return float(self.speed) * t_land

    def safe_max_gap(self, rise: float = 0.0) -> float:
        return self.horizontal_reach(rise) * float(self.reach_x_ratio)

    def safe_max_rise(self) -> float:
        return self.max_jump_height * float(self.reach_y_ratio)

    def to_overrides(self) -> Dict[str, float]:
        return {
            "speed": float(self.speed),
            "jump_velocity": float(self.jump_velocity),
            "gravity": float(self.gravity),
            "coyote_time": float(self.coyote_time),
            "jump_buffer": float(self.jump_buffer),
            "fall_limit": float(self.fall_limit),
        }


@dataclass
class Box:
    """轴对齐矩形（局部坐标：中心 + 尺寸）。"""

    x: float
    y: float
    w: float
    h: float
    role: str = "platform"
    name: str = ""

    @property
    def left(self) -> float:
        return self.x - self.w / 2.0

    @property
    def right(self) -> float:
        return self.x + self.w / 2.0

    @property
    def top(self) -> float:
        return self.y - self.h / 2.0

    @property
    def bottom(self) -> float:
        return self.y + self.h / 2.0

    def overlaps(self, other: Box, *, pad: float = 4.0) -> bool:
        return not (
            self.right + pad <= other.left
            or self.left - pad >= other.right
            or self.bottom + pad <= other.top
            or self.top - pad >= other.bottom
        )


@dataclass
class PlacedEntity:
    name: str
    role: str
    x: float
    y: float
    w: float = 100.0
    h: float = 16.0
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LayoutPlan:
    feel: FeelProfile
    ground: Box
    player: PlacedEntity
    platforms: List[PlacedEntity] = field(default_factory=list)
    enemies: List[PlacedEntity] = field(default_factory=list)
    pickups: List[PlacedEntity] = field(default_factory=list)
    decorations: List[PlacedEntity] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def stats(self) -> Dict[str, Any]:
        return {
            "max_jump_height": round(self.feel.max_jump_height, 1),
            "max_same_level_reach": round(self.feel.max_same_level_reach, 1),
            "safe_max_gap": round(self.feel.safe_max_gap(0.0), 1),
            "platform_count": len(self.platforms),
            "pickup_count": len(self.pickups),
            "enemy_count": len(self.enemies),
            "notes": list(self.notes),
        }


def resolve_feel(
    *,
    width: int = 640,
    height: int = 360,
    genre: str = "platformer",
    difficulty: str = "medium",
    overrides: Optional[Dict[str, Any]] = None,
) -> FeelProfile:
    """按品类/难度给出可玩默认手感，再用 overrides / 视口微调。"""
    feel = FeelProfile()

    # 品类基线（预览路径与 godot_templates 的 GDM 覆盖前默认对齐）
    if genre in ("platformer", "runner"):
        feel = FeelProfile(speed=190.0, jump_velocity=-430.0, gravity=980.0)
    elif genre in ("shooter", "arena", "fighting"):
        feel = FeelProfile(speed=210.0, jump_velocity=-380.0, gravity=980.0)
    elif genre in ("rpg", "adventure", "metroidvania"):
        feel = FeelProfile(speed=170.0, jump_velocity=-400.0, gravity=980.0)
    elif genre in ("puzzle", "simulation"):
        feel = FeelProfile(speed=140.0, jump_velocity=-320.0, gravity=900.0)

    # 难度：easy 更宽松跳跃、hard 更紧凑
    if difficulty == "easy":
        feel.jump_velocity *= 1.12
        feel.speed *= 0.95
        feel.reach_x_ratio = 0.68
    elif difficulty == "hard":
        feel.jump_velocity *= 0.92
        feel.speed *= 1.05
        feel.reach_x_ratio = 0.78

    # 死亡线：比地面顶再低一截，站桩/落地不误杀；掉出世界才判死
    feel.fall_limit = float(height // 2 + 48)

    if overrides:
        for key in ("speed", "jump_velocity", "gravity", "coyote_time", "jump_buffer", "fall_limit"):
            if key in overrides and overrides[key] is not None:
                try:
                    setattr(feel, key, float(overrides[key]))
                except (TypeError, ValueError):
                    continue
    return feel


def _try_place(box: Box, occupied: List[Box], *, pad: float = 6.0) -> bool:
    if any(box.overlaps(o, pad=pad) for o in occupied):
        return False
    occupied.append(box)
    return True


def plan_platformer_layout(
    *,
    width: int,
    height: int,
    feel: FeelProfile,
    platform_count: int = 5,
    pickup_count: int = 4,
    enemy_count: int = 2,
    decoration_count: int = 3,
    layout_seed: int = 0,
) -> LayoutPlan:
    """侧视角平台跳跃：可跳可达的平台链 + 出生点/敌人/拾取安全放置。"""
    rng = random.Random(layout_seed)
    half_w = width / 2.0
    half_h = height / 2.0

    ground_h = 24.0
    ground_top = half_h - ground_h  # 地面顶面局部 y
    ground = Box(0.0, half_h - ground_h / 2.0, float(width), ground_h, role="ground", name="Ground")

    # 出生点：靠左地面，远离右缘
    player_w, player_h = 32.0, 48.0
    spawn_x = -half_w + 100.0
    spawn_y = ground_top - player_h / 2.0 - 1.0
    player = PlacedEntity("Player", "player", spawn_x, spawn_y, player_w, player_h)

    occupied: List[Box] = [
        Box(ground.x, ground.y, ground.w, ground.h, role="ground", name="Ground"),
        Box(player.x, player.y, player.w + 8, player.h + 8, role="player", name="Player"),
    ]

    max_rise = feel.safe_max_rise()
    min_gap = 70.0
    platforms: List[PlacedEntity] = []

    # 第一平台：出生点右侧，保证开局可跳上
    first_gap = rng.uniform(min_gap, min(130.0, feel.safe_max_gap(40.0) * 0.7))
    px = spawn_x + first_gap + 50.0
    py = ground_top - rng.uniform(50.0, min(90.0, max_rise * 0.55)) - 8.0
    first = Box(px, py, 110.0, 16.0, role="platform", name="Platform0")
    if _try_place(first, occupied, pad=8.0):
        platforms.append(PlacedEntity(first.name, "platform", first.x, first.y, first.w, first.h))
        cursor = first
    else:
        cursor = Box(spawn_x + 120.0, ground_top - 40.0, 100.0, 16.0, role="platform", name="Platform0")
        if _try_place(cursor, occupied, pad=8.0):
            platforms.append(PlacedEntity(cursor.name, "platform", cursor.x, cursor.y, cursor.w, cursor.h))

    # 后续平台：沿链推进，间隙/升高都受跳跃包络约束
    for i in range(1, max(1, platform_count)):
        prev = cursor if "cursor" in dir() else None
        if prev is None:
            break
        rise = rng.uniform(-10.0, max(12.0, max_rise * 0.55))
        rise = max(-max_rise * 0.35, min(rise, max_rise * 0.85))
        gap = rng.uniform(min_gap, max(min_gap + 20.0, feel.safe_max_gap(max(0.0, rise)) * 0.85))
        w = rng.choice([90.0, 100.0, 110.0, 120.0])
        # 交替左右微抖，避免纯直线呆板
        x = prev.right + gap + w / 2.0
        if x + w / 2.0 > half_w - 20.0:
            # 回卷到左侧区域继续，仍保持相对高度链
            x = -half_w + 40.0 + w / 2.0
            rise = rng.uniform(-20.0, max(10.0, max_rise * 0.4))
        y = prev.y - rise
        # 夹紧在可玩带内
        y = max(-half_h + 80.0, min(y, ground_top - 28.0))
        box = Box(x, y, w, 16.0, role="platform", name=f"Platform{i + 1}")
        placed = False
        for _attempt in range(6):
            if _try_place(box, occupied, pad=8.0):
                platforms.append(PlacedEntity(box.name, "platform", box.x, box.y, box.w, box.h))
                cursor = box
                placed = True
                break
            box.x += rng.uniform(24.0, 48.0)
            box.y -= rng.uniform(8.0, 24.0)
            if box.x + box.w / 2.0 > half_w - 16.0:
                box.x = -half_w + 40.0 + box.w / 2.0
        if not placed:
            # 最后兜底：夹在可跳范围内的空位
            for t in range(8):
                tx = prev.right + min_gap + 40 + t * 30
                ty = ground_top - 70 - (t % 3) * 25
                cand = Box(tx, ty, 90.0, 16.0, role="platform", name=f"Platform{i + 1}")
                if _try_place(cand, occupied, pad=6.0):
                    platforms.append(PlacedEntity(cand.name, "platform", cand.x, cand.y, cand.w, cand.h))
                    cursor = cand
                    break

    # 拾取物：优先落在平台上空，其次地面附近
    pickups: List[PlacedEntity] = []
    for i in range(pickup_count):
        if platforms:
            p = platforms[i % len(platforms)]
            x = p.x + rng.uniform(-p.w * 0.25, p.w * 0.25)
            y = p.y - 28.0 - rng.uniform(0.0, 18.0)
        else:
            x = spawn_x + 80 + i * 70
            y = ground_top - 40 - (i % 2) * 20
        box = Box(x, y, 20.0, 20.0, role="pickup", name=f"Pickup{i + 1}")
        if _try_place(box, occupied, pad=4.0):
            pickups.append(PlacedEntity(box.name, "pickup", box.x, box.y, 20.0, 20.0))

    # 敌人：地面右侧，距出生点足够远，巡逻范围落在地面
    enemies: List[PlacedEntity] = []
    for i in range(enemy_count):
        for _try in range(8):
            x = rng.uniform(spawn_x + 180.0, half_w - 60.0)
            y = ground_top - 14.0 - 1.0
            patrol = rng.uniform(50.0, 90.0)
            box = Box(x, y, 28.0, 28.0, role="enemy", name=f"Enemy{i + 1}")
            if box.left < spawn_x + 140:
                continue
            if _try_place(box, occupied, pad=10.0):
                enemies.append(
                    PlacedEntity(box.name, "enemy", box.x, box.y, 28.0, 28.0, meta={"range": patrol})
                )
                break

    # 装饰：地面沿线，不与实体重叠
    decorations: List[PlacedEntity] = []
    for i in range(decoration_count):
        for _try in range(6):
            x = rng.uniform(-half_w + 40.0, half_w - 40.0)
            y = ground_top - 16.0 - rng.uniform(0.0, 8.0)
            box = Box(x, y, 16.0, 32.0, role="decoration", name=f"Prop{i + 1}")
            if _try_place(box, occupied, pad=6.0):
                decorations.append(PlacedEntity(box.name, "decoration", box.x, box.y, 16.0, 32.0))
                break

    notes = [
        f"jump_h≈{feel.max_jump_height:.0f}",
        f"reach≈{feel.max_same_level_reach:.0f}",
        f"safe_gap≈{feel.safe_max_gap(0):.0f}",
    ]
    return LayoutPlan(
        feel=feel,
        ground=ground,
        player=player,
        platforms=platforms,
        enemies=enemies,
        pickups=pickups,
        decorations=decorations,
        notes=notes,
    )


def plan_arena_layout(
    *,
    width: int,
    height: int,
    feel: FeelProfile,
    enemy_count: int = 3,
    pickup_count: int = 5,
    decoration_count: int = 4,
    layout_seed: int = 0,
) -> LayoutPlan:
    """竞技场：极坐标散布 + 最小间距，保证不与玩家出生点重叠。"""
    rng = random.Random(layout_seed)
    half_w = width / 2.0
    half_h = height / 2.0
    ground_h = 24.0
    ground = Box(0.0, half_h - ground_h / 2.0, float(width), ground_h, role="ground", name="Ground")
    player = PlacedEntity("Player", "player", -half_w + 80.0, half_h - 80.0, 32.0, 48.0)

    occupied = [
        Box(ground.x, ground.y, ground.w, ground.h, role="ground"),
        Box(player.x, player.y, 48.0, 56.0, role="player"),
    ]
    platforms: List[PlacedEntity] = []
    enemies: List[PlacedEntity] = []
    pickups: List[PlacedEntity] = []
    decorations: List[PlacedEntity] = []

    # 中心小台
    mid = Box(0.0, 20.0, 120.0, 16.0, role="platform", name="Platform1")
    if _try_place(mid, occupied, pad=8.0):
        platforms.append(PlacedEntity(mid.name, "platform", mid.x, mid.y, mid.w, mid.h))

    radius = min(half_w, half_h) * 0.55
    for i in range(max(1, enemy_count)):
        angle = 2 * 3.14159265 * i / max(1, enemy_count) + rng.uniform(-0.15, 0.15)
        x = radius * math.cos(angle)
        y = 10.0 + radius * 0.45 * math.sin(angle)
        y = max(-half_h + 60.0, min(y, ground_top_safe(half_h, 40.0)))
        box = Box(x, y, 28.0, 28.0, role="enemy", name=f"Enemy{i + 1}")
        if _try_place(box, occupied, pad=10.0):
            enemies.append(PlacedEntity(box.name, "enemy", box.x, box.y, 28.0, 28.0, meta={"range": 70.0}))

    for i in range(pickup_count):
        angle = 2 * 3.14159265 * (i + 0.5) / max(1, pickup_count)
        x = radius * 0.85 * math.cos(angle)
        y = 30.0 + radius * 0.35 * math.sin(angle)
        box = Box(x, y, 20.0, 20.0, role="pickup", name=f"Pickup{i + 1}")
        if _try_place(box, occupied, pad=4.0):
            pickups.append(PlacedEntity(box.name, "pickup", box.x, box.y, 20.0, 20.0))

    for i in range(decoration_count):
        x = rng.uniform(-half_w + 40, half_w - 40)
        y = ground_top_safe(half_h, 20.0) - 16.0
        box = Box(x, y, 16.0, 32.0, role="decoration", name=f"Prop{i + 1}")
        if _try_place(box, occupied, pad=6.0):
            decorations.append(PlacedEntity(box.name, "decoration", box.x, box.y, 16.0, 32.0))

    return LayoutPlan(
        feel=feel,
        ground=ground,
        player=player,
        platforms=platforms,
        enemies=enemies,
        pickups=pickups,
        decorations=decorations,
        notes=["arena_layout"],
    )


def ground_top_safe(half_h: float, margin: float) -> float:
    return half_h - 24.0 - margin


def plan_layout(
    scene_ir: Any,
    *,
    width: int = 640,
    height: int = 360,
    layout_seed: int = 0,
    feel_overrides: Optional[Dict[str, Any]] = None,
) -> LayoutPlan:
    """入口：按 SceneIR 品类选择布局策略，返回可直接写入 .tscn 的坐标。"""
    genre = getattr(scene_ir, "genre", "platformer") or "platformer"
    difficulty = getattr(scene_ir, "difficulty", "medium") or "medium"
    layout = getattr(scene_ir, "layout", "linear") or "linear"
    entities = list(getattr(scene_ir, "entities", []) or [])

    def _count(role: str, default: int) -> int:
        total = 0
        for e in entities:
            if getattr(e, "role", None) == role:
                total += max(1, int(getattr(e, "count", 1) or 1))
        return total if total > 0 else default

    feel = resolve_feel(
        width=width, height=height, genre=genre, difficulty=difficulty, overrides=feel_overrides
    )

    # 网格品类不需要实体链布局
    if genre in GRID_GENRES:
        ground = Box(0.0, height / 2.0 - 12.0, float(width), 24.0, role="ground", name="Ground")
        return LayoutPlan(
            feel=feel,
            ground=ground,
            player=PlacedEntity("Player", "player", 0.0, 0.0),
            notes=["grid_genre_skip_entity_layout"],
        )

    if layout in ("arena", "top_down") or genre in ("arena", "shooter", "fighting", "pong"):
        return plan_arena_layout(
            width=width,
            height=height,
            feel=feel,
            enemy_count=_count("enemy", 3),
            pickup_count=_count("pickup", 5),
            decoration_count=_count("decoration", 4),
            layout_seed=layout_seed,
        )

    return plan_platformer_layout(
        width=width,
        height=height,
        feel=feel,
        platform_count=max(3, _count("platform", 5)),
        pickup_count=max(2, _count("pickup", 4)),
        enemy_count=max(1, _count("enemy", 2)),
        decoration_count=max(2, _count("decoration", 3)),
        layout_seed=layout_seed,
    )


def validate_plan(plan: LayoutPlan) -> Dict[str, Any]:
    """布局门禁：平台间隙是否落在安全跳远内（便于 playtest/eval 读取）。"""
    boxes = [plan.ground]
    boxes.extend(
        Box(p.x, p.y, p.w, p.h, role="platform", name=p.name) for p in plan.platforms
    )
    issues: List[str] = []
    # 相邻平台（按 x 排序）间隙
    plats = sorted(plan.platforms, key=lambda p: p.x)
    for a, b in zip(plats, plats[1:]):
        gap = (b.x - b.w / 2.0) - (a.x + a.w / 2.0)
        if gap < 0:
            continue  # 回卷场景，跳过负间隙
        rise = max(0.0, a.y - b.y)  # b 更高时 rise 为正（y 向下为正的局部？）
        # 局部 y 向下为正：更高 = y 更小；rise = a.y - b.y 若 b 更高则为正
        allowed = plan.feel.safe_max_gap(rise)
        if gap > allowed:
            issues.append(f"gap_too_large:{a.name}->{b.name}:{gap:.0f}>{allowed:.0f}")
    return {"ok": not issues, "issues": issues, **plan.stats()}
