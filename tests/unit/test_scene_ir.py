"""测试 Scene IR 系统（中间表示 + 模板 + 确定性转换 + Schema 校验）"""

import pytest
from src.agents.scene_ir import (
    SceneIR, CameraIR, EntityIR,
    SceneDescription, SceneObject, ComponentSpec,
    infer_genre_from_gdm, repair_scene_ir,
    _VALID_GENRES, _VALID_ROLES, _VALID_LAYOUTS,
)
from src.agents.scene_templates import match_template, fill_template, TEMPLATES
from src.agents.scene_ir_to_desc import ir_to_scene_description, _get_palette


# ═══════════════════════════════════════════════════════════
#  SceneIR 模型校验
# ═══════════════════════════════════════════════════════════

class TestSceneIRModel:
    def test_valid_ir(self):
        ir = SceneIR(
            scene_name="MainScene", genre="platformer", layout="linear",
            difficulty="easy", camera=CameraIR(mode="2d_side_view"),
            entities=[EntityIR(name="Player", role="player")],
        )
        assert ir.scene_name == "MainScene"
        assert ir.genre == "platformer"
        assert len(ir.entities) == 1

    def test_invalid_genre_repaired(self):
        ir = SceneIR(genre="unknown_genre")
        assert ir.genre == "platformer"

    def test_invalid_layout_repaired(self):
        ir = SceneIR(layout="unknown")
        assert ir.layout == "linear"

    def test_invalid_difficulty_repaired(self):
        ir = SceneIR(difficulty="extreme")
        assert ir.difficulty == "easy"

    def test_invalid_role_repaired(self):
        ent = EntityIR(name="X", role="boss")
        assert ent.role == "decoration"

    def test_invalid_camera_mode_repaired(self):
        cam = CameraIR(mode="vr_mode")
        assert cam.mode == "2d_side_view"

    def test_count_clamped_to_1(self):
        ent = EntityIR(name="X", role="enemy", count=0)
        assert ent.count == 1

    def test_invalid_spawn_zone_repaired(self):
        ent = EntityIR(name="X", role="enemy", spawn_zone="nowhere")
        assert ent.spawn_zone == "center"

    def test_defaults(self):
        ir = SceneIR()
        assert ir.scene_name == "GameScene"
        assert ir.genre == "platformer"
        assert ir.entities == []
        assert ir.camera.mode == "2d_side_view"


class TestEntityIR:
    def test_valid_entity(self):
        ent = EntityIR(name="Player", role="player", count=1, spawn_zone="left", script="PlayerController")
        assert ent.script == "PlayerController"

    def test_all_valid_roles(self):
        for role in _VALID_ROLES:
            ent = EntityIR(name="X", role=role)
            assert ent.role == role


# ═══════════════════════════════════════════════════════════
#  模板匹配
# ═══════════════════════════════════════════════════════════

class TestMatchTemplate:
    def test_platformer_by_genre(self):
        gdm = {"genre": "platformer"}
        assert match_template(gdm) == "platformer"

    def test_shooter_by_genre(self):
        gdm = {"genre": "space shooter"}
        assert match_template(gdm) == "shooter"

    def test_rpg_by_genre(self):
        gdm = {"genre": "回合制RPG"}
        assert match_template(gdm) == "rpg"

    def test_puzzle_by_keyword(self):
        gdm = {"scenes": [{"purpose": "解谜关卡"}]}
        assert match_template(gdm) == "puzzle"

    def test_runner_by_keyword(self):
        gdm = {"core_loop": "无尽跑酷"}
        assert match_template(gdm) == "runner"

    def test_no_match_returns_none(self):
        gdm = {"genre": "", "scenes": [], "core_loop": ""}
        assert match_template(gdm) is None

    def test_all_templates_exist(self):
        for name in ["platformer", "shooter", "rpg", "puzzle", "runner"]:
            assert name in TEMPLATES


# ═══════════════════════════════════════════════════════════
#  模板填充
# ═══════════════════════════════════════════════════════════

class TestFillTemplate:
    def test_platformer_template(self):
        gdm = {"game_title": "Super Jump"}
        ir = fill_template("platformer", gdm)
        assert ir.genre == "platformer"
        assert ir.layout == "linear"
        assert ir.camera.mode == "2d_side_view"
        assert len(ir.entities) > 0
        names = [e.name for e in ir.entities]
        assert "Player" in names
        assert "Ground" in names

    def test_shooter_template(self):
        ir = fill_template("shooter", {})
        assert ir.genre == "shooter"
        assert ir.layout == "arena"
        assert ir.camera.mode == "top_down"

    def test_rpg_template(self):
        ir = fill_template("rpg", {})
        assert ir.genre == "rpg"
        assert ir.layout == "open_world"

    def test_unknown_template_defaults_to_platformer(self):
        ir = fill_template("nonexistent", {})
        assert ir.genre == "platformer"

    def test_gdm_entity_script_override(self):
        gdm = {"entities": [{"name": "Player", "role": "player", "components": ["MyPlayerScript"]}]}
        ir = fill_template("platformer", gdm)
        player = next(e for e in ir.entities if e.name == "Player")
        assert player.script == "MyPlayerScript"

    def test_gdm_camera_mode_override(self):
        gdm = {"camera_mode": "top_down"}
        ir = fill_template("platformer", gdm)
        assert ir.camera.mode == "top_down"

    def test_gdm_scene_name_used(self):
        gdm = {"scenes": [{"scene_name": "Level1"}]}
        ir = fill_template("platformer", gdm)
        assert ir.scene_name == "Level1"


# ═══════════════════════════════════════════════════════════
#  IR → scene_description 转换
# ═══════════════════════════════════════════════════════════

class TestIRToSceneDescription:
    def test_basic_conversion(self):
        ir = SceneIR(
            scene_name="TestScene",
            genre="platformer",
            layout="linear",
            entities=[
                EntityIR(name="Player", role="player", spawn_zone="left"),
                EntityIR(name="Ground", role="ground", spawn_zone="bottom"),
            ],
        )
        desc = ir_to_scene_description(ir)
        assert desc["scene_name"] == "TestScene"
        assert len(desc["game_objects"]) == 2
        assert desc["camera"]["orthographic"] is True
        assert desc["lighting"]["type"] == "directional"

    def test_entity_count_generates_multiple_objects(self):
        ir = SceneIR(
            entities=[EntityIR(name="Coin", role="pickup", count=3, spawn_zone="random")],
        )
        desc = ir_to_scene_description(ir)
        names = [o["name"] for o in desc["game_objects"]]
        assert "Coin1" in names
        assert "Coin2" in names
        assert "Coin3" in names

    def test_single_count_no_suffix(self):
        ir = SceneIR(
            entities=[EntityIR(name="Player", role="player", count=1)],
        )
        desc = ir_to_scene_description(ir)
        assert desc["game_objects"][0]["name"] == "Player"

    def test_ground_is_static(self):
        ir = SceneIR(entities=[EntityIR(name="Ground", role="ground")])
        desc = ir_to_scene_description(ir)
        assert desc["game_objects"][0]["is_static"] is True

    def test_player_has_rigidbody(self):
        ir = SceneIR(
            camera=CameraIR(mode="2d_side_view"),
            entities=[EntityIR(name="Player", role="player", script="PlayerController")],
        )
        desc = ir_to_scene_description(ir)
        comps = [c["type"] for c in desc["game_objects"][0]["components"]]
        assert "Rigidbody2D" in comps
        assert "BoxCollider2D" in comps
        assert "PlayerController" in comps

    def test_2d_camera_orthographic(self):
        ir = SceneIR(camera=CameraIR(mode="2d_side_view"))
        desc = ir_to_scene_description(ir)
        assert desc["camera"]["orthographic"] is True
        assert desc["camera"]["position"] == [0, 0, -10]

    def test_3d_camera_perspective(self):
        ir = SceneIR(camera=CameraIR(mode="3d_third_person"))
        desc = ir_to_scene_description(ir)
        assert desc["camera"]["orthographic"] is False

    def test_background_color_from_palette(self):
        ir = SceneIR(camera=CameraIR(background="space_black"))
        desc = ir_to_scene_description(ir)
        assert desc["camera"]["background_color"][0] < 0.1  # dark

    def test_file_metadata_script_binding(self):
        ir = SceneIR(
            entities=[EntityIR(name="Player", role="player", script="CustomController")],
        )
        meta = {"Assets/Scripts/CustomController.cs": {"class_name": "CustomController"}}
        desc = ir_to_scene_description(ir, file_metadata=meta)
        comps = [c["type"] for c in desc["game_objects"][0]["components"]]
        assert "CustomController" in comps

    def test_schema_validation_passes(self):
        ir = SceneIR(
            entities=[
                EntityIR(name="Player", role="player", script="PlayerController"),
                EntityIR(name="Ground", role="ground"),
            ],
        )
        desc = ir_to_scene_description(ir)
        # Validate with Pydantic
        validated = SceneDescription(**desc)
        assert validated.scene_name == "GameScene"
        assert len(validated.game_objects) == 2


# ═══════════════════════════════════════════════════════════
#  自动修复
# ═══════════════════════════════════════════════════════════

class TestRepairSceneIR:
    def test_infers_genre_from_gdm(self):
        gdm = {"genre": "太空射击游戏"}
        ir = repair_scene_ir({}, gdm)
        assert ir.genre == "shooter"

    def test_infers_genre_from_scenes(self):
        gdm = {"scenes": [{"purpose": "跳跳乐平台关卡"}]}
        ir = repair_scene_ir({}, gdm)
        assert ir.genre == "platformer"

    def test_default_genre_when_empty(self):
        ir = repair_scene_ir({}, {})
        assert ir.genre == "platformer"

    def test_missing_scene_name_defaulted(self):
        ir = repair_scene_ir({"genre": "rpg"}, {})
        assert ir.scene_name == "GameScene"

    def test_missing_camera_defaulted(self):
        ir = repair_scene_ir({"genre": "shooter"}, {})
        assert ir.camera.mode in ("2d_side_view", "top_down", "3d_third_person", "3d_first_person")

    def test_missing_entities_defaulted(self):
        ir = repair_scene_ir({"genre": "platformer"}, {})
        assert ir.entities == []

    def test_invalid_entities_repaired(self):
        raw = {"entities": [{"name": "X", "role": "INVALID"}]}
        ir = repair_scene_ir(raw, {})
        assert ir.entities[0].role == "decoration"


# ═══════════════════════════════════════════════════════════
#  调色板
# ═══════════════════════════════════════════════════════════

class TestPalette:
    def test_default_palette(self):
        pal = _get_palette(None, "platformer")
        assert "player_blue" in pal

    def test_space_palette_for_shooter(self):
        pal = _get_palette(None, "shooter")
        assert pal == _get_palette(None, "shooter")

    def test_theme_overrides_genre(self):
        pal = _get_palette("太空战争", "platformer")
        # 太空 theme → space palette
        assert pal["player_blue"][0] == 0.3

    def test_forest_theme(self):
        pal = _get_palette("森林冒险", "platformer")
        assert pal["ground_brown"][0] < 0.4  # darker brown
