"""测试 Unity 完整项目模板生成器"""

import json
import pytest
from src.engine.unity.project_generator import (
    UnityProjectGenerator,
    _deterministic_guid,
)


@pytest.fixture
def generator():
    return UnityProjectGenerator()


@pytest.fixture
def base_state():
    """最小可用 GameDevState"""
    return {
        "project_context": {
            "engine": "unity",
            "project_name": "TestPlatformer",
            "requirements": "2D platformer",
        },
        "game_design_model": {
            "game_title": "Test Platformer",
            "camera_mode": "2d_orthographic",
            "tags_layers": {
                "tags": ["Player", "Enemy", "Coin"],
                "layers": [
                    {"name": "Ground", "index": 8},
                    {"name": "Player", "index": 9},
                ],
            },
            "input_map": [
                {"name": "Horizontal", "type": "axis", "description": "Move"},
                {"name": "Jump", "type": "button", "key": "Space"},
                {"name": "Fire1", "type": "button", "key": "Left Ctrl"},
                {"name": "Dash", "type": "button", "key": "Left Shift"},
            ],
            "physics_settings": {"gravity": -9.81},
        },
        "code_generated": {
            "Assets/Scripts/Player/PlayerController.cs": "public class PlayerController : MonoBehaviour {}",
            "Assets/Scripts/Core/GameManager.cs": "public class GameManager : MonoBehaviour {}",
        },
        "scene_description": {"scene_name": "MainScene", "game_objects": []},
    }


class TestGenerateAll:
    """入口方法 generate_all 的测试"""

    def test_returns_expected_keys(self, generator, base_state):
        files = generator.generate_all(base_state)
        assert "Packages/manifest.json" in files
        assert "ProjectSettings/ProjectSettings.asset" in files
        assert "ProjectSettings/TagManager.asset" in files
        assert "ProjectSettings/InputManager.asset" in files
        assert "ProjectSettings/QualitySettings.asset" in files
        assert "ProjectSettings/EditorBuildSettings.asset" in files

    def test_2d_project_includes_physics2d(self, generator, base_state):
        files = generator.generate_all(base_state)
        assert "ProjectSettings/Physics2DSettings.asset" in files

    def test_3d_project_no_physics2d(self, generator, base_state):
        base_state["game_design_model"]["camera_mode"] = "3d_third_person"
        base_state["game_design_model"]["physics_settings"] = {}
        base_state["code_generated"] = {
            "Assets/Scripts/Player/PlayerController.cs": "public class PlayerController : MonoBehaviour {}",
        }
        files = generator.generate_all(base_state)
        assert "ProjectSettings/Physics2DSettings.asset" not in files

    def test_meta_files_match_cs_files(self, generator, base_state):
        files = generator.generate_all(base_state)
        cs_files = [f for f in files if f.endswith(".cs") and not f.endswith(".meta")]
        meta_files = [f for f in files if f.endswith(".cs.meta")]
        for cs in cs_files:
            assert cs + ".meta" in meta_files, f"Missing .meta for {cs}"

    def test_meta_not_generated_for_non_cs(self, generator, base_state):
        base_state["code_generated"]["Assets/Scenes/scene.json"] = "{}"
        files = generator.generate_all(base_state)
        assert "Assets/Scenes/scene.json.meta" not in files


class TestManifestJson:
    """Packages/manifest.json 测试"""

    def test_valid_json(self, generator):
        content = generator._generate_manifest_json()
        data = json.loads(content)
        assert "dependencies" in data

    def test_contains_core_packages(self, generator):
        content = generator._generate_manifest_json()
        data = json.loads(content)
        deps = data["dependencies"]
        assert "com.unity.2d.sprite" in deps
        assert "com.unity.modules.physics2d" in deps
        assert "com.unity.modules.animation" in deps
        assert "com.unity.ugui" in deps


class TestTagManager:
    """ProjectSettings/TagManager.asset 测试"""

    def test_contains_custom_tags(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_tag_manager(gdm)
        assert "- Enemy" in content
        assert "- Coin" in content

    def test_excludes_builtin_tags(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_tag_manager(gdm)
        # Player is a built-in Unity tag, should not appear as custom tag
        lines = content.split("\n")
        tag_section = False
        for line in lines:
            if line.strip() == "tags:":
                tag_section = True
            elif tag_section and line.strip() == "layers:":
                break
            elif tag_section and line.strip() == "- Player":
                pytest.fail("Built-in tag 'Player' should not be in custom tags")

    def test_contains_custom_layers(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_tag_manager(gdm)
        assert "Ground" in content
        assert "Player" in content

    def test_builtin_layers_preserved(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_tag_manager(gdm)
        assert "Default" in content
        assert "TransparentFX" in content
        assert "Ignore Raycast" in content
        assert "Water" in content
        assert "UI" in content

    def test_empty_gdm(self, generator):
        content = generator._generate_tag_manager({})
        assert "TagManager:" in content
        assert "tags:" in content
        assert "layers:" in content


class TestInputManager:
    """ProjectSettings/InputManager.asset 测试"""

    def test_contains_default_axes(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_input_manager(gdm)
        assert "m_Name: Horizontal" in content
        assert "m_Name: Vertical" in content
        assert "m_Name: Fire1" in content
        assert "m_Name: Jump" in content
        assert "m_Name: Mouse X" in content
        assert "m_Name: Mouse Y" in content

    def test_contains_custom_axes(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_input_manager(gdm)
        assert "m_Name: Dash" in content

    def test_no_duplicate_axes(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_input_manager(gdm)
        assert content.count("m_Name: Horizontal") == 1
        assert content.count("m_Name: Jump") == 1
        assert content.count("m_Name: Fire1") == 1

    def test_empty_gdm(self, generator):
        content = generator._generate_input_manager({})
        assert "m_Name: Horizontal" in content


class TestMetaFiles:
    """.meta 文件生成测试"""

    def test_meta_format(self, generator, base_state):
        files = generator._generate_meta_files(base_state["code_generated"])
        meta = files["Assets/Scripts/Player/PlayerController.cs.meta"]
        assert meta.startswith("fileFormatVersion: 2")
        assert "guid:" in meta
        assert "MonoImporter:" in meta

    def test_meta_guid_deterministic(self, generator):
        code = {"Assets/Scripts/Player/PlayerController.cs": "..."}
        files1 = generator._generate_meta_files(code)
        files2 = generator._generate_meta_files(code)
        guid1 = files1["Assets/Scripts/Player/PlayerController.cs.meta"].split("guid: ")[1].split("\n")[0]
        guid2 = files2["Assets/Scripts/Player/PlayerController.cs.meta"].split("guid: ")[1].split("\n")[0]
        assert guid1 == guid2

    def test_different_paths_different_guids(self, generator):
        code = {
            "Assets/Scripts/A.cs": "...",
            "Assets/Scripts/B.cs": "...",
        }
        files = generator._generate_meta_files(code)
        guid_a = files["Assets/Scripts/A.cs.meta"].split("guid: ")[1].split("\n")[0]
        guid_b = files["Assets/Scripts/B.cs.meta"].split("guid: ")[1].split("\n")[0]
        assert guid_a != guid_b


class TestProjectSettings:
    """ProjectSettings.asset 测试"""

    def test_contains_product_name(self, generator):
        content = generator._generate_project_settings("MyGame")
        assert "productName: MyGame" in content

    def test_contains_company_name(self, generator):
        content = generator._generate_project_settings("X")
        assert "companyName: GameForge" in content


class TestEditorBuildSettings:
    """EditorBuildSettings.asset 测试"""

    def test_contains_scene(self, generator):
        scene = {"scene_name": "MainScene"}
        content = generator._generate_editor_build_settings(scene)
        assert "Assets/Scenes/MainScene.unity" in content

    def test_default_scene_name(self, generator):
        content = generator._generate_editor_build_settings({})
        assert "Assets/Scenes/SampleScene.unity" in content


class TestPhysics2DSettings:
    """Physics2DSettings.asset 测试"""

    def test_contains_gravity(self, generator, base_state):
        gdm = base_state["game_design_model"]
        content = generator._generate_physics2d_settings(gdm)
        assert "y: -9.81" in content

    def test_custom_gravity(self, generator):
        gdm = {"physics_settings": {"gravity": -15.0}}
        content = generator._generate_physics2d_settings(gdm)
        assert "y: -15.0" in content

    def test_default_gravity(self, generator):
        content = generator._generate_physics2d_settings({})
        assert "y: -9.81" in content


class TestIs2DProject:
    """2D/3D 项目判断测试"""

    def test_2d_by_camera_mode(self, generator):
        gdm = {"camera_mode": "2d_orthographic"}
        assert generator._is_2d_project(gdm, {}) is True

    def test_3d_by_camera_mode(self, generator):
        gdm = {"camera_mode": "3d_third_person"}
        assert generator._is_2d_project(gdm, {}) is False

    def test_2d_by_rigidbody2d(self, generator):
        gdm = {}
        code = {"a.cs": "GetComponent<Rigidbody2D>()"}
        assert generator._is_2d_project(gdm, code) is True

    def test_2d_by_spriterenderer(self, generator):
        gdm = {}
        code = {"a.cs": "GetComponent<SpriteRenderer>()"}
        assert generator._is_2d_project(gdm, code) is True

    def test_2d_by_physics_type(self, generator):
        gdm = {"physics_settings": {"type": "2d"}}
        assert generator._is_2d_project(gdm, {}) is True


class TestDeterministicGuid:
    """GUID 确定性测试"""

    def test_same_path_same_guid(self):
        g1 = _deterministic_guid("Assets/Scripts/A.cs")
        g2 = _deterministic_guid("Assets/Scripts/A.cs")
        assert g1 == g2

    def test_different_path_different_guid(self):
        g1 = _deterministic_guid("Assets/Scripts/A.cs")
        g2 = _deterministic_guid("Assets/Scripts/B.cs")
        assert g1 != g2

    def test_guid_is_32_hex(self):
        guid = _deterministic_guid("test")
        assert len(guid) == 32
        assert all(c in "0123456789abcdef" for c in guid)
