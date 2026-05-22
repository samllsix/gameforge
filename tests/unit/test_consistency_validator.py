"""测试代码与场景一致性校验器"""

import pytest
from src.utils.consistency_validator import (
    validate_code_scene_consistency,
    ValidationResult,
    _extract_classes_from_code,
    _extract_scripts_from_scene,
    _is_unity_builtin,
)


class TestValidationResult:
    def test_empty_result(self):
        result = ValidationResult()
        assert not result.has_errors
        assert not result.has_issues
        assert result.to_dict()["error_count"] == 0

    def test_with_errors(self):
        result = ValidationResult(errors=[{"type": "test", "message": "err"}])
        assert result.has_errors
        assert result.has_issues

    def test_with_warnings_only(self):
        result = ValidationResult(warnings=[{"type": "test", "message": "warn"}])
        assert not result.has_errors
        assert result.has_issues


class TestExtractClasses:
    def test_extract_public_class(self):
        code_files = {"Assets/Scripts/Player.cs": "public class Player : MonoBehaviour {}"}
        result = _extract_classes_from_code(code_files)
        assert "Assets/Scripts/Player.cs" in result
        assert result["Assets/Scripts/Player.cs"] == "Player"

    def test_extract_partial_class(self):
        code_files = {"Assets/Scripts/GameManager.cs": "public partial class GameManager {}"}
        result = _extract_classes_from_code(code_files)
        assert result["Assets/Scripts/GameManager.cs"] == "GameManager"

    def test_ignore_non_cs(self):
        code_files = {"Assets/README.md": "# readme"}
        result = _extract_classes_from_code(code_files)
        assert len(result) == 0


class TestExtractScripts:
    def test_extract_custom_scripts(self):
        scene = {
            "game_objects": [
                {
                    "name": "Player",
                    "components": [
                        {"type": "PlayerController"},
                        {"type": "Rigidbody2D"},
                    ],
                }
            ]
        }
        scripts = _extract_scripts_from_scene(scene)
        assert "PlayerController" in scripts
        assert "Rigidbody2D" not in scripts

    def test_extract_from_children(self):
        scene = {
            "game_objects": [
                {
                    "name": "Parent",
                    "components": [],
                    "children": [
                        {
                            "name": "Child",
                            "components": [{"type": "EnemyAI"}],
                        }
                    ],
                }
            ]
        }
        scripts = _extract_scripts_from_scene(scene)
        assert "EnemyAI" in scripts


class TestIsUnityBuiltin:
    def test_builtin_components(self):
        assert _is_unity_builtin("Rigidbody2D")
        assert _is_unity_builtin("BoxCollider")
        assert _is_unity_builtin("SpriteRenderer")
        assert _is_unity_builtin("Camera")

    def test_custom_scripts(self):
        assert not _is_unity_builtin("PlayerController")
        assert not _is_unity_builtin("GameManager")


class TestValidateConsistency:
    def test_missing_script_error(self):
        code_files = {"Assets/Scripts/Player.cs": "public class Player {}"}
        scene = {
            "game_objects": [
                {"name": "Player", "components": [{"type": "Player"}]},
                {"name": "Enemy", "components": [{"type": "EnemyController"}]},
            ]
        }
        result = validate_code_scene_consistency(code_files, scene)
        assert result.has_errors
        assert any(e["type"] == "missing_script" for e in result.errors)

    def test_class_filename_mismatch_warning(self):
        code_files = {"Assets/Scripts/Player.cs": "public class PlayerController {}"}
        scene = {"game_objects": []}
        result = validate_code_scene_consistency(code_files, scene)
        assert any(w["type"] == "class_filename_mismatch" for w in result.warnings)

    def test_valid_consistency(self):
        code_files = {
            "Assets/Scripts/Player.cs": "public class Player : MonoBehaviour {}",
            "Assets/Scripts/Enemy.cs": "public class Enemy : MonoBehaviour {}",
        }
        scene = {
            "game_objects": [
                {"name": "Player", "components": [{"type": "Player"}]},
                {"name": "Enemy", "components": [{"type": "Enemy"}]},
            ]
        }
        result = validate_code_scene_consistency(code_files, scene)
        assert not result.has_errors

    def test_builtin_not_error(self):
        code_files = {"Assets/Scripts/Player.cs": "public class Player {}"}
        scene = {
            "game_objects": [
                {
                    "name": "Player",
                    "components": [
                        {"type": "Player"},
                        {"type": "Rigidbody2D"},
                        {"type": "BoxCollider2D"},
                    ],
                }
            ]
        }
        result = validate_code_scene_consistency(code_files, scene)
        assert not result.has_errors

    def test_lifecycle_spelling_warning(self):
        code_files = {
            "Assets/Scripts/Player.cs": """
public class Player : MonoBehaviour {
    void update() {}
    void ontriggerenter() {}
}
"""
        }
        scene = {"game_objects": []}
        result = validate_code_scene_consistency(code_files, scene)
        spelling_warnings = [w for w in result.warnings if w["type"] == "lifecycle_spelling"]
        assert len(spelling_warnings) >= 2

    def test_invalid_namespace_error(self):
        code_files = {
            "Assets/Scripts/Bad.cs": "namespace 123invalid { public class Bad {} }"
        }
        scene = {"game_objects": []}
        result = validate_code_scene_consistency(code_files, scene)
        assert any(e["type"] == "invalid_namespace" for e in result.errors)

    def test_with_gdm_tags(self):
        code_files = {
            "Assets/Scripts/Player.cs": 'public class Player { void Start() { CompareTag("Enemy"); } }'
        }
        scene = {"game_objects": []}
        gdm = {"tags_layers": {"tags": ["Player", "Enemy"], "layers": []}}
        result = validate_code_scene_consistency(code_files, scene, gdm=gdm)
        assert not any("Tag" in s for s in result.suggestions)

    def test_missing_tag_suggestion(self):
        code_files = {
            "Assets/Scripts/Player.cs": 'public class Player { void Start() { CompareTag("CustomTag"); } }'
        }
        scene = {"game_objects": []}
        gdm = {"tags_layers": {"tags": ["Player"], "layers": []}, "input_map": []}
        result = validate_code_scene_consistency(code_files, scene, gdm=gdm)
        assert any("CustomTag" in s for s in result.suggestions)

    def test_with_file_metadata(self):
        code_files = {
            "Assets/Scripts/Player.cs": "public class Player {}",
            "Assets/Scripts/HealthSystem.cs": "public class HealthSystem {}",
        }
        scene = {
            "game_objects": [
                {
                    "name": "Player",
                    "components": [{"type": "Player"}],
                }
            ]
        }
        file_metadata = {
            "Assets/Scripts/Player.cs": {
                "required_components": ["HealthSystem"]
            }
        }
        result = validate_code_scene_consistency(
            code_files, scene, file_metadata=file_metadata
        )
        # Player object missing HealthSystem component (not a Unity built-in)
        assert any(w["type"] == "missing_component" for w in result.warnings)
