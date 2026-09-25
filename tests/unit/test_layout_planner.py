"""智能布局规划器与手感包络单元测试。"""

from __future__ import annotations

from src.agents.scene_ir import CameraIR, EntityIR, SceneIR
from src.engine.godot.layout_planner import (
    FeelProfile,
    plan_layout,
    plan_platformer_layout,
    resolve_feel,
    validate_plan,
)


def test_feel_jump_envelope_formulas():
    feel = FeelProfile(speed=200.0, jump_velocity=-400.0, gravity=1000.0)
    # h = v²/(2g) = 160000/2000 = 80
    assert abs(feel.max_jump_height - 80.0) < 1e-6
    # t_apex = 0.4s, same-level reach = 200 * 0.8 = 160
    assert abs(feel.max_same_level_reach - 160.0) < 1e-6
    # 上升 40 时水平距离应小于同层跳远
    assert feel.horizontal_reach(40.0) < feel.max_same_level_reach
    assert feel.horizontal_reach(1000.0) == 0.0


def test_resolve_feel_overrides_and_difficulty():
    base = resolve_feel(genre="platformer", difficulty="medium")
    easy = resolve_feel(genre="platformer", difficulty="easy")
    hard = resolve_feel(genre="platformer", difficulty="hard")
    assert abs(easy.jump_velocity) > abs(base.jump_velocity)
    assert abs(hard.jump_velocity) < abs(base.jump_velocity)

    custom = resolve_feel(overrides={"speed": 250.0, "jump_velocity": -500.0})
    assert custom.speed == 250.0
    assert custom.jump_velocity == -500.0


def test_platformer_layout_places_player_on_ground_and_chain():
    plan = plan_platformer_layout(
        width=640, height=360, feel=resolve_feel(), platform_count=5, layout_seed=42
    )
    assert plan.player.y < plan.ground.top  # 玩家在地面之上
    assert plan.platforms, "至少应放置 1 个平台"
    assert len(plan.platforms) >= 1
    # 平台之间不重叠（宽松 pad=0）
    for i, a in enumerate(plan.platforms):
        for b in plan.platforms[i + 1 :]:
            ax1, ax2 = a.x - a.w / 2, a.x + a.w / 2
            bx1, bx2 = b.x - b.w / 2, b.x + b.w / 2
            ay1, ay2 = a.y - a.h / 2, a.y + a.h / 2
            by1, by2 = b.y - b.h / 2, b.y + b.h / 2
            overlap = not (ax2 <= bx1 or ax1 >= bx2 or ay2 <= by1 or ay1 >= by2)
            assert not overlap, f"{a.name} overlaps {b.name}"


def test_layout_deterministic_with_seed():
    a = plan_platformer_layout(width=640, height=360, feel=FeelProfile(), layout_seed=7)
    b = plan_platformer_layout(width=640, height=360, feel=FeelProfile(), layout_seed=7)
    assert [(p.x, p.y) for p in a.platforms] == [(p.x, p.y) for p in b.platforms]


def test_plan_layout_platformer_and_validate():
    ir = SceneIR(
        scene_name="GameScene",
        genre="platformer",
        layout="linear",
        difficulty="easy",
        camera=CameraIR(),
        entities=[
            EntityIR(name="Player", role="player"),
            EntityIR(name="Ground", role="ground"),
            EntityIR(name="Platform", role="platform", count=4),
            EntityIR(name="Coin", role="pickup", count=3),
            EntityIR(name="Slime", role="enemy", count=2),
        ],
    )
    plan = plan_layout(ir, width=640, height=360, layout_seed=1)
    report = validate_plan(plan)
    assert report["ok"] is True, report["issues"]
    assert report["platform_count"] >= 1


def test_plan_layout_grid_skips_entity_chain():
    ir = SceneIR(scene_name="S", genre="snake", layout="grid", entities=[])
    plan = plan_layout(ir, width=640, height=360)
    assert "grid_genre_skip_entity_layout" in plan.notes


def test_build_scene_tscn_uses_smart_feel_and_camera():
    from src.engine.godot.scene_to_godot import build_scene_tscn

    ir = SceneIR(
        scene_name="GameScene",
        genre="platformer",
        layout="linear",
        difficulty="medium",
        camera=CameraIR(),
        entities=[
            EntityIR(name="Player", role="player"),
            EntityIR(name="Ground", role="ground"),
            EntityIR(name="Platform", role="platform", count=4),
            EntityIR(name="Coin", role="pickup", count=3),
            EntityIR(name="Enemy", role="enemy", count=1),
        ],
    )
    tscn = build_scene_tscn(ir, width=640, height=360, layout_seed=3)
    assert "Ground" in tscn
    assert "speed = " in tscn
    # 手感不再是旧硬编码 120/-260/600
    assert "speed = 120.0" not in tscn
    assert "jump_velocity = -260.0" not in tscn
    assert "camera_follow.gd" in tscn
    assert "Camera2D" in tscn
    assert "coyote_time" in tscn
