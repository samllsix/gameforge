"""GameForge - Godot 兼容性验证器

验证生成的 GDScript 代码是否与 Godot 引擎兼容。
检查项：
1. GDScript 语法基础检查
2. 节点类型有效性
3. 信号使用正确性
4. 命名规范
"""

import re
from typing import Any, Dict, List, Set, Tuple
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class GodotValidationResult:
    """Godot 兼容性验证结果"""
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": not self.has_errors,
            "errors": self.errors,
            "warnings": self.warnings,
        }


# Godot 内置节点类型
GODOT_NODE_TYPES = {
    # 2D
    "Node2D", "Sprite2D", "AnimatedSprite2D", "CharacterBody2D",
    "RigidBody2D", "StaticBody2D", "Area2D", "Camera2D",
    "TileMap", "PointLight2D", "DirectionalLight2D",
    "CollisionShape2D", "CollisionPolygon2D",
    "Path2D", "PathFollow2D", "Line2D",
    "ParallaxBackground", "ParallaxLayer",
    "NavigationRegion2D", "NavigationAgent2D",
    # 3D
    "Node3D", "MeshInstance3D", "CharacterBody3D",
    "RigidBody3D", "StaticBody3D", "Area3D", "Camera3D",
    "DirectionalLight3D", "OmniLight3D", "SpotLight3D",
    "CollisionShape3D", "CollisionPolygon3D",
    "CSGBox3D", "CSGSphere3D",
    "GPUParticles3D", "CPUParticles3D",
    "NavigationRegion3D", "NavigationAgent3D",
    # UI
    "Control", "CanvasLayer", "Container",
    "HBoxContainer", "VBoxContainer", "GridContainer",
    "MarginContainer", "PanelContainer", "ScrollContainer",
    "Label", "Button", "LineEdit", "TextEdit", "RichTextLabel",
    "TextureRect", "NinePatchRect", "ColorRect",
    "ProgressBar", "HSlider", "VSlider",
    "CheckBox", "CheckButton", "OptionButton",
    "MenuButton", "PopupMenu", "Tree", "ItemList",
    # 通用
    "Node", "Timer", "Tween", "AnimationPlayer", "AnimationTree",
    "AudioStreamPlayer", "AudioStreamPlayer2D", "AudioStreamPlayer3D",
}

# GDScript 命名规范
CLASS_NAME_PATTERN = re.compile(r'^class_name\s+(\w+)', re.MULTILINE)
FUNC_PATTERN = re.compile(r'^func\s+(\w+)', re.MULTILINE)
VAR_PATTERN = re.compile(r'^(?:@export\s+)?var\s+(\w+)', re.MULTILINE)
SIGNAL_PATTERN = re.compile(r'^signal\s+(\w+)', re.MULTILINE)
CONST_PATTERN = re.compile(r'^const\s+(\w+)', re.MULTILINE)


def validate_godot_compatibility(code_files: Dict[str, str]) -> GodotValidationResult:
    """验证 GDScript 代码的 Godot 兼容性

    Args:
        code_files: 代码文件字典 {路径: 内容}

    Returns:
        验证结果
    """
    result = GodotValidationResult()
    gd_files = {p: c for p, c in code_files.items() if p.endswith(".gd")}

    for path, content in gd_files.items():
        _validate_gdscript_file(path, content, result)

    return result


def _validate_gdscript_file(path: str, content: str, result: GodotValidationResult):
    """验证单个 GDScript 文件"""
    lines = content.split("\n")

    # 1. 缩进检查
    _check_indentation(path, lines, result)

    # 2. 括号平衡检查
    _check_brackets(path, content, result)

    # 3. 函数定义检查
    _check_functions(path, lines, result)

    # 4. 命名规范检查
    _check_naming(path, content, result)

    # 5. 常见错误模式检查
    _check_common_patterns(path, content, result)


def _check_indentation(path: str, lines: List[str], result: GodotValidationResult):
    """检查缩进一致性"""
    has_tab = False
    has_space = False

    for line in lines:
        if line.startswith("\t"):
            has_tab = True
        elif line.startswith("    "):
            has_space = True

    if has_tab and has_space:
        result.errors.append({
            "check": "indentation",
            "message": f"缩进不一致：混用了 Tab 和空格: {path}",
            "file": path,
        })


def _check_brackets(path: str, content: str, result: GodotValidationResult):
    """检查括号平衡"""
    open_parens = content.count("(")
    close_parens = content.count(")")
    if open_parens != close_parens:
        result.errors.append({
            "check": "brackets",
            "message": f"小括号不匹配 ( {open_parens} vs ) {close_parens}: {path}",
            "file": path,
        })

    open_brackets = content.count("[")
    close_brackets = content.count("]")
    if open_brackets != close_brackets:
        result.errors.append({
            "check": "brackets",
            "message": f"方括号不匹配 [ {open_brackets} vs ] {close_brackets}: {path}",
            "file": path,
        })


def _check_functions(path: str, lines: List[str], result: GodotValidationResult):
    """检查函数定义"""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("func "):
            if ":" not in stripped:
                result.errors.append({
                    "check": "syntax",
                    "message": f"第{i+1}行: func 定义缺少冒号: {path}",
                    "file": path,
                    "line": i + 1,
                })
            # 检查是否有返回类型注解
            if "->" not in stripped and "pass" not in stripped:
                result.warnings.append({
                    "check": "type_hint",
                    "message": f"第{i+1}行: 函数缺少返回类型注解: {path}",
                    "file": path,
                    "line": i + 1,
                })


def _check_naming(path: str, content: str, result: GodotValidationResult):
    """检查命名规范"""
    # 检查 class_name 是否 PascalCase
    class_match = CLASS_NAME_PATTERN.search(content)
    if class_match:
        class_name = class_match.group(1)
        if not class_name[0].isupper():
            result.warnings.append({
                "check": "naming",
                "message": f"class_name 应使用 PascalCase: {class_name} ({path})",
                "file": path,
            })

    # 检查函数名是否 snake_case
    for match in FUNC_PATTERN.finditer(content):
        func_name = match.group(1)
        if func_name.startswith("_"):
            func_name = func_name[1:]
        if func_name != func_name.lower() and "_" not in func_name:
            result.warnings.append({
                "check": "naming",
                "message": f"函数名应使用 snake_case: {match.group(1)} ({path})",
                "file": path,
            })

    # 检查常量名是否 UPPER_SNAKE_CASE
    for match in CONST_PATTERN.finditer(content):
        const_name = match.group(1)
        if const_name != const_name.upper():
            result.warnings.append({
                "check": "naming",
                "message": f"常量名应使用 UPPER_SNAKE_CASE: {const_name} ({path})",
                "file": path,
            })


def _check_common_patterns(path: str, content: str, result: GodotValidationResult):
    """检查常见错误模式"""
    lines = content.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检查 get_node() 在 _process 中使用（性能问题）
        if "_process" in line or "_physics_process" in line:
            # 检查后续行是否有 get_node 调用
            for j in range(i + 1, min(i + 20, len(lines))):
                if "get_node(" in lines[j] or "get_node(" in lines[j]:
                    result.warnings.append({
                        "check": "performance",
                        "message": f"第{j+1}行: 在 _process 中使用 get_node()，建议使用 @onready: {path}",
                        "file": path,
                        "line": j + 1,
                    })
                    break

        # 检查信号连接是否使用了正确的语法
        if ".connect(" in stripped:
            if "(" not in stripped.split(".connect(")[1]:
                result.warnings.append({
                    "check": "signal",
                    "message": f"第{i+1}行: 信号连接语法可能不正确: {path}",
                    "file": path,
                    "line": i + 1,
                })
