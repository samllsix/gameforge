"""Tests for the Unity compatibility gate."""

from src.utils.unity_compatibility_validator import validate_unity_compatibility


def test_valid_project_passes():
    code_files = {
        "Assets/Scripts/Player/PlayerController.cs": (
            "using UnityEngine; public class PlayerController : MonoBehaviour {}"
        )
    }
    scene = {
        "game_objects": [
            {
                "name": "Player",
                "components": [
                    {"type": "Rigidbody2D"},
                    {"type": "PlayerController"},
                ],
            }
        ]
    }

    result = validate_unity_compatibility(code_files, scene)

    assert not result.has_errors
    assert result.to_dict()["passed"] is True


def test_runtime_test_script_is_error():
    code_files = {
        "Assets/Scripts/Player/PlayerControllerTests.cs": (
            "using NUnit.Framework; public class PlayerControllerTests {}"
        )
    }

    result = validate_unity_compatibility(code_files, {"game_objects": []})

    assert result.has_errors
    assert result.errors[0]["check"] == "runtime_test_script"


def test_missing_scene_script_is_error():
    code_files = {
        "Assets/Scripts/Player/PlayerController.cs": (
            "using UnityEngine; public class PlayerController : MonoBehaviour {}"
        )
    }
    scene = {
        "game_objects": [
            {"name": "Enemy", "components": [{"type": "EnemyController"}]}
        ]
    }

    result = validate_unity_compatibility(code_files, scene)

    assert result.has_errors
    assert any(e["check"] == "missing_scene_script" for e in result.errors)


def test_missing_ienumerator_using_is_error():
    code_files = {
        "Assets/Scripts/Foo/Foo.cs": (
            "using UnityEngine; public class Foo : MonoBehaviour { IEnumerator Run() { yield return null; } }"
        )
    }

    result = validate_unity_compatibility(code_files, {"game_objects": []})

    assert any(e["check"] == "missing_using" for e in result.errors)


def test_monobehaviour_class_filename_mismatch_is_error():
    code_files = {
        "Assets/Scripts/Player/Player.cs": (
            "using UnityEngine; public class PlayerController : MonoBehaviour {}"
        )
    }

    result = validate_unity_compatibility(code_files, {"game_objects": []})

    assert any(e["check"] == "class_filename_mismatch" for e in result.errors)


def test_duplicate_script_name_is_error():
    code_files = {
        "Assets/Scripts/A/PlayerController.cs": (
            "using UnityEngine; public class PlayerController : MonoBehaviour {}"
        ),
        "Assets/Scripts/B/PlayerController.cs": (
            "using UnityEngine; public class PlayerController : MonoBehaviour {}"
        ),
    }

    result = validate_unity_compatibility(code_files, {"game_objects": []})

    assert any(e["check"] == "duplicate_script_class" for e in result.errors)
