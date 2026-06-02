"""Unity compatibility gate for generated projects.

This validator catches issues that are likely to make Unity fail compilation or
enter Safe Mode before the generated files are imported into a Unity project.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from src.utils.consistency_validator import _is_unity_builtin


@dataclass
class UnityCompatibilityResult:
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_issues(self) -> bool:
        return bool(self.errors or self.warnings or self.suggestions)

    def add_error(self, check: str, message: str, **details: Any) -> None:
        self.errors.append({"check": check, "message": message, **details})

    def add_warning(self, check: str, message: str, **details: Any) -> None:
        self.warnings.append({"check": check, "message": message, **details})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": not self.has_errors,
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


def validate_unity_compatibility(
    code_files: Dict[str, str],
    scene_desc: Dict[str, Any] | None = None,
    gdm: Dict[str, Any] | None = None,
) -> UnityCompatibilityResult:
    result = UnityCompatibilityResult()
    scene_desc = scene_desc or {}
    gdm = gdm or {}

    runtime_scripts = {
        path.replace("\\", "/"): content
        for path, content in code_files.items()
        if _is_runtime_script(path)
    }
    classes_by_name = _extract_classes(runtime_scripts)

    _check_runtime_tests(code_files, result)
    _check_compile_risks(runtime_scripts, result)
    _check_class_filename_contract(runtime_scripts, classes_by_name, result)
    _check_duplicate_script_names(classes_by_name, result)
    _check_scene_script_references(scene_desc, classes_by_name, result)
    _check_tags_layers_inputs(code_files, scene_desc, gdm, result)

    return result


def _is_runtime_script(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized.endswith(".cs")
        and not normalized.endswith("Tests.cs")
        and not normalized.startswith("Assets/Tests/")
        and not normalized.startswith("Assets/Editor/")
    )


def _check_runtime_tests(
    code_files: Dict[str, str], result: UnityCompatibilityResult
) -> None:
    for path, content in code_files.items():
        normalized = path.replace("\\", "/")
        if not normalized.endswith(".cs"):
            continue
        is_test_code = (
            normalized.endswith("Tests.cs")
            or "using NUnit.Framework" in content
            or "UnityEngine.TestTools" in content
            or "[UnityTest]" in content
        )
        if is_test_code and not normalized.startswith("Assets/Tests/"):
            result.add_error(
                "runtime_test_script",
                "Unity test scripts must be under Assets/Tests, not runtime script folders.",
                file=normalized,
            )


def _check_compile_risks(
    scripts: Dict[str, str], result: UnityCompatibilityResult
) -> None:
    for path, content in scripts.items():
        if content.count("{") != content.count("}"):
            result.add_error(
                "brace_balance",
                "C# braces are not balanced.",
                file=path,
                open_count=content.count("{"),
                close_count=content.count("}"),
            )

        if "IEnumerator" in content and "using System.Collections" not in content:
            result.add_error(
                "missing_using",
                "IEnumerator requires using System.Collections.",
                file=path,
                namespace="System.Collections",
            )

        if "List<" in content and "using System.Collections.Generic" not in content:
            result.add_error(
                "missing_using",
                "List<T> requires using System.Collections.Generic.",
                file=path,
                namespace="System.Collections.Generic",
            )

        if re.search(r"\b(TMP_|TextMeshPro)", content) and "using TMPro" not in content:
            result.add_error(
                "missing_using",
                "TextMeshPro types require using TMPro.",
                file=path,
                namespace="TMPro",
            )

        namespace = _extract_namespace(content)
        if namespace and not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", namespace):
            result.add_error(
                "invalid_namespace",
                "Namespace is not a valid C# namespace.",
                file=path,
                namespace=namespace,
            )


def _check_class_filename_contract(
    scripts: Dict[str, str],
    classes_by_name: Dict[str, List[Dict[str, str]]],
    result: UnityCompatibilityResult,
) -> None:
    for class_name, entries in classes_by_name.items():
        for entry in entries:
            path = entry["file"]
            file_name = path.rsplit("/", 1)[-1].replace(".cs", "")
            content = scripts[path]
            if class_name != file_name:
                severity = result.add_error if _inherits_mono_behaviour(content) else result.add_warning
                severity(
                    "class_filename_mismatch",
                    "Primary public class name should match the .cs file name.",
                    file=path,
                    class_name=class_name,
                    file_name=file_name,
                )


def _check_duplicate_script_names(
    classes_by_name: Dict[str, List[Dict[str, str]]],
    result: UnityCompatibilityResult,
) -> None:
    for class_name, entries in classes_by_name.items():
        if len(entries) > 1:
            result.add_error(
                "duplicate_script_class",
                "Duplicate public script class names make Unity component binding ambiguous.",
                class_name=class_name,
                files=[entry["file"] for entry in entries],
            )


def _check_scene_script_references(
    scene_desc: Dict[str, Any],
    classes_by_name: Dict[str, List[Dict[str, str]]],
    result: UnityCompatibilityResult,
) -> None:
    generated = set(classes_by_name)
    for script_name in _extract_scene_scripts(scene_desc):
        if script_name not in generated:
            result.add_error(
                "missing_scene_script",
                "Scene references a script that is not generated.",
                script=script_name,
            )


def _check_tags_layers_inputs(
    code_files: Dict[str, str],
    scene_desc: Dict[str, Any],
    gdm: Dict[str, Any],
    result: UnityCompatibilityResult,
) -> None:
    defined_tags = set(gdm.get("tags_layers", {}).get("tags", []))
    builtin_tags = {"Untagged", "MainCamera", "Player", "Respawn", "Finish", "EditorOnly"}
    used_tags = _extract_code_tags(code_files) | _extract_scene_tags(scene_desc)
    for tag in sorted(used_tags):
        if tag and tag not in builtin_tags and tag not in defined_tags:
            result.suggestions.append(
                f"Tag '{tag}' is used but not listed in the Game Design Model."
            )

    defined_layers = {
        layer.get("name", "")
        for layer in gdm.get("tags_layers", {}).get("layers", [])
        if isinstance(layer, dict)
    }
    for layer in sorted(_extract_code_layers(code_files)):
        if layer and layer not in defined_layers:
            result.suggestions.append(
                f"Layer '{layer}' is used but not listed in the Game Design Model."
            )

    defined_inputs = {entry.get("name", "") for entry in gdm.get("input_map", [])}
    builtin_inputs = {"Horizontal", "Vertical", "Fire1", "Fire2", "Fire3", "Jump", "Mouse X", "Mouse Y"}
    for input_name in sorted(_extract_code_inputs(code_files)):
        if input_name not in builtin_inputs and input_name not in defined_inputs:
            result.suggestions.append(
                f"Input '{input_name}' is used but not listed in the Game Design Model."
            )


def _extract_classes(scripts: Dict[str, str]) -> Dict[str, List[Dict[str, str]]]:
    classes: Dict[str, List[Dict[str, str]]] = {}
    for path, content in scripts.items():
        match = re.search(
            r"public\s+(?:partial\s+)?(?:class|struct|interface)\s+(\w+)",
            content,
        )
        if match:
            classes.setdefault(match.group(1), []).append(
                {"file": path, "namespace": _extract_namespace(content)}
            )
    return classes


def _extract_namespace(content: str) -> str:
    match = re.search(r"\bnamespace\s+([^\s{;]+)", content)
    return match.group(1) if match else ""


def _inherits_mono_behaviour(content: str) -> bool:
    return bool(re.search(r":\s*MonoBehaviour\b", content))


def _extract_scene_scripts(scene_desc: Dict[str, Any]) -> Set[str]:
    scripts: Set[str] = set()

    def visit(obj: Dict[str, Any]) -> None:
        for comp in obj.get("components", []) or []:
            comp_type = comp.get("type", "")
            if comp_type and not _is_unity_builtin(comp_type):
                scripts.add(comp_type)
        for child in obj.get("children", []) or []:
            visit(child)

    for obj in scene_desc.get("game_objects", []) or []:
        visit(obj)
    return scripts


def _extract_code_tags(code_files: Dict[str, str]) -> Set[str]:
    tags: Set[str] = set()
    for content in code_files.values():
        tags.update(re.findall(r'CompareTag\("([^"]+)"\)', content))
        tags.update(re.findall(r'\.tag\s*=\s*"([^"]+)"', content))
    return tags


def _extract_scene_tags(scene_desc: Dict[str, Any]) -> Set[str]:
    return {
        obj.get("tag", "")
        for obj in scene_desc.get("game_objects", []) or []
        if obj.get("tag")
    }


def _extract_code_layers(code_files: Dict[str, str]) -> Set[str]:
    layers: Set[str] = set()
    for content in code_files.values():
        layers.update(re.findall(r'LayerMask\.GetMask\("([^"]+)"\)', content))
    return layers


def _extract_code_inputs(code_files: Dict[str, str]) -> Set[str]:
    inputs: Set[str] = set()
    for content in code_files.values():
        inputs.update(
            re.findall(r'Input\.Get(?:Axis|Button)(?:Down|Up|Raw)?\("([^"]+)"\)', content)
        )
    return inputs
