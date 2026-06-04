from src.utils.unified_validator import UnifiedValidationResult, validate_all


def test_empty_input_returns_warning():
    result = validate_all({})

    assert result.has_errors is False
    assert result.has_issues is True
    assert result.to_dict()["passed"] is True
    assert result.to_dict()["warning_count"] == 1


def test_detects_basic_csharp_syntax_errors():
    result = validate_all(
        {
            "Assets/Scripts/Broken.cs": (
                "using UnityEngine;\n"
                "public class Broken : MonoBehaviour {\n"
                "    void Start() {\n"
            )
        }
    )

    assert result.has_errors is True
    checks = {error["check"] for error in result.errors}
    assert "syntax" in checks


def test_detects_missing_unity_using_for_monobehaviour():
    result = validate_all(
        {
            "Assets/Scripts/PlayerController.cs": (
                "public class PlayerController : MonoBehaviour {\n"
                "    void Start() {}\n"
                "}\n"
            )
        }
    )

    assert any(error["check"] == "unity_compatibility" for error in result.errors)


def test_result_to_dict_counts_issues():
    result = UnifiedValidationResult(
        errors=[{"check": "syntax", "message": "bad"}],
        warnings=[{"check": "naming", "message": "warn"}],
        suggestions=["fix it"],
    )

    data = result.to_dict()

    assert data["passed"] is False
    assert data["error_count"] == 1
    assert data["warning_count"] == 1
