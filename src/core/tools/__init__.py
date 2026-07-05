"""GameForge - 核心工具模块

提供文件操作、代码分析、项目管理等工具函数。
专注于 Godot/GDScript 生态。
"""

import os
import re
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


def read_file(file_path: str, encoding: str = "utf-8") -> Optional[str]:
    """安全读取文件内容"""
    try:
        with open(file_path, "r", encoding=encoding) as f:
            return f.read()
    except (IOError, UnicodeDecodeError):
        return None


def write_file(file_path: str, content: str, encoding: str = "utf-8") -> bool:
    """安全写入文件内容"""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding=encoding) as f:
            f.write(content)
        return True
    except IOError:
        return False


def list_files(directory: str, extensions: Optional[List[str]] = None) -> List[str]:
    """列出目录下的文件"""
    files = []
    if not os.path.isdir(directory):
        return files

    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if extensions:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in extensions:
                    continue
            files.append(os.path.join(root, filename))

    return sorted(files)


def extract_code_blocks(content: str, language: str = "gdscript") -> List[Dict[str, str]]:
    """从文本中提取代码块

    Args:
        content: 文本内容
        language: 代码语言 (gdscript, gds, gd)

    Returns:
        代码块列表，每个包含file_path和content
    """
    blocks = []
    # 支持 gdscript, gds, gd 以及通用标记
    pattern = rf'```(?:{language}|gds?|gdscript)?\s*\n(.*?)\n```'
    matches = re.findall(pattern, content, re.DOTALL)

    for match in matches:
        match = match.strip()
        if not match:
            continue

        # 尝试提取文件路径注释 (GDScript: ## 文件: path 或 # 文件: path)
        file_path_match = re.search(r'#\s*(?:文件|File):\s*(\S+)', match)
        if file_path_match:
            file_path = file_path_match.group(1)
            match = re.sub(r'#\s*(?:文件|File):\s*\S+\s*\n', '', match, count=1).strip()
        else:
            file_path = None

        blocks.append({"file_path": file_path, "content": match})

    return blocks


def calculate_code_metrics(content: str) -> Dict[str, Any]:
    """计算 GDScript 代码度量指标

    Args:
        content: GDScript 代码内容

    Returns:
        度量指标字典
    """
    lines = content.split("\n")
    total_lines = len(lines)
    blank_lines = sum(1 for line in lines if not line.strip())
    comment_lines = sum(1 for line in lines if line.strip().startswith("#"))
    code_lines = total_lines - blank_lines - comment_lines

    # GDScript 函数定义
    methods = re.findall(r'^func\s+\w+', content, re.MULTILINE)
    # GDScript 类定义 (class_name 或 extends)
    classes = re.findall(r'^(?:class_name\s+\w+|extends\s+\w+)', content, re.MULTILINE)
    # 内部类
    inner_classes = re.findall(r'^class\s+\w+', content, re.MULTILINE)

    max_indent = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            indent = len(line) - len(stripped)
            max_indent = max(max_indent, indent)

    return {
        "total_lines": total_lines,
        "code_lines": code_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines,
        "comment_ratio": comment_lines / total_lines if total_lines > 0 else 0,
        "method_count": len(methods),
        "class_count": len(classes) + len(inner_classes),
        "max_indent_depth": max_indent // 4,  # GDScript 默认 4 空格缩进
        "avg_line_length": sum(len(line) for line in lines) / total_lines if total_lines > 0 else 0,
    }


def validate_gdscript_syntax(content: str) -> Tuple[bool, List[str]]:
    """基础 GDScript 语法验证

    Args:
        content: GDScript 代码内容

    Returns:
        (是否通过验证, 错误列表)
    """
    errors = []

    # 检查缩进一致性（GDScript 使用 Tab 或空格，不能混用）
    has_tab_indent = False
    has_space_indent = False
    for line in content.split("\n"):
        if line.startswith("\t"):
            has_tab_indent = True
        elif line.startswith("    "):
            has_space_indent = True

    if has_tab_indent and has_space_indent:
        errors.append("缩进不一致：混用了 Tab 和空格缩进")

    # 检查括号平衡
    open_parens = content.count("(")
    close_parens = content.count(")")
    if open_parens != close_parens:
        errors.append(f"小括号不匹配: ( = {open_parens}, ) = {close_parens}")

    open_brackets = content.count("[")
    close_brackets = content.count("]")
    if open_brackets != close_brackets:
        errors.append(f"方括号不匹配: [ = {open_brackets}, ] = {close_brackets}")

    # 检查函数定义格式
    for i, line in enumerate(content.split("\n")):
        stripped = line.strip()
        if stripped.startswith("func "):
            if ":" not in stripped:
                errors.append(f"第{i+1}行: func 定义缺少冒号")

    return len(errors) == 0, errors


def generate_file_hash(content: str) -> str:
    """生成文件内容哈希"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除非法字符"""
    illegal_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(illegal_chars, "_", filename)
    sanitized = sanitized.strip(". ")
    return sanitized or "unnamed"


# Godot 内置节点类型集合
GODOT_BUILTINS: frozenset = frozenset({
    # 2D 节点
    "Node2D", "Sprite2D", "AnimatedSprite2D", "TileMap",
    "CharacterBody2D", "RigidBody2D", "StaticBody2D", "Area2D",
    "Camera2D", "ParallaxBackground", "ParallaxLayer",
    "PointLight2D", "DirectionalLight2D", "Light2D",
    "Line2D", "MultiMeshInstance2D", "NavigationRegion2D",
    "NavigationAgent2D", "NavigationObstacle2D",
    "Path2D", "PathFollow2D", "RemoteTransform2D",
    # 3D 节点
    "Node3D", "MeshInstance3D", "CSGBox3D", "CSGSphere3D",
    "CharacterBody3D", "RigidBody3D", "StaticBody3D", "Area3D",
    "Camera3D", "DirectionalLight3D", "OmniLight3D", "SpotLight3D",
    "NavigationRegion3D", "NavigationAgent3D", "NavigationObstacle3D",
    "Path3D", "PathFollow3D", "RemoteTransform3D",
    "GPUParticles3D", "CPUParticles3D",
    # UI 节点
    "Control", "CanvasLayer", "Container", "BoxContainer",
    "HBoxContainer", "VBoxContainer", "GridContainer",
    "FlowContainer", "MarginContainer", "TabContainer",
    "PanelContainer", "ScrollContainer", "SplitContainer",
    "Label", "Button", "LineEdit", "TextEdit", "RichTextLabel",
    "TextureRect", "NinePatchRect", "ColorRect", "VideoStreamPlayer",
    "ProgressBar", "HSlider", "VSlider", "HScrollBar", "VScrollBar",
    "SpinBox", "CheckBox", "CheckButton", "OptionButton",
    "MenuButton", "PopupMenu", "Tree", "ItemList",
    "TabBar", "GraphNode", "GraphEdit",
    # 通用节点
    "Node", "Timer", "Tween", "AnimationPlayer", "AnimationTree",
    "AudioStreamPlayer", "AudioStreamPlayer2D", "AudioStreamPlayer3D",
    "NavigationAgent2D", "NavigationAgent3D",
    # 碰撞形状
    "CollisionShape2D", "CollisionPolygon2D",
    "CollisionShape3D", "CollisionPolygon3D",
    # 资源
    "SpriteFrames", "Animation", "AudioStream",
    "Shader", "ShaderMaterial", "StandardMaterial3D",
    "PhysicsMaterial", "BoxShape2D", "CircleShape2D",
    "RectangleShape2D", "CapsuleShape2D", "SegmentShape2D",
    "BoxShape3D", "SphereShape3D", "CapsuleShape3D",
})


def is_godot_builtin(type_name: str) -> bool:
    """检查是否是 Godot 内置节点类型

    Args:
        type_name: 类型名称

    Returns:
        是否是 Godot 内置类型
    """
    return type_name in GODOT_BUILTINS


# 预编译的正则表达式
_GDSCRIPT_CLASS_PATTERN = re.compile(r'^class_name\s+(\w+)', re.MULTILINE)
_GDSCRIPT_EXTENDS_PATTERN = re.compile(r'^extends\s+(\w+)', re.MULTILINE)
_GDSCRIPT_FUNC_PATTERN = re.compile(r'^func\s+(\w+)', re.MULTILINE)
_GDSCRIPT_SIGNAL_PATTERN = re.compile(r'^signal\s+(\w+)', re.MULTILINE)
_GDSCRIPT_VAR_PATTERN = re.compile(r'^(?:@export\s+)?var\s+(\w+)', re.MULTILINE)


def extract_class_name(content: str) -> Optional[str]:
    """从 GDScript 代码中提取 class_name

    Args:
        content: GDScript 代码内容

    Returns:
        类名，未找到返回 None
    """
    match = _GDSCRIPT_CLASS_PATTERN.search(content)
    return match.group(1) if match else None


def extract_extends(content: str) -> Optional[str]:
    """从 GDScript 代码中提取 extends 类型

    Args:
        content: GDScript 代码内容

    Returns:
        继承类型，未找到返回 None
    """
    match = _GDSCRIPT_EXTENDS_PATTERN.search(content)
    return match.group(1) if match else None


def extract_functions(content: str) -> List[str]:
    """从 GDScript 代码中提取所有函数名"""
    return _GDSCRIPT_FUNC_PATTERN.findall(content)


def extract_signals(content: str) -> List[str]:
    """从 GDScript 代码中提取所有信号名"""
    return _GDSCRIPT_SIGNAL_PATTERN.findall(content)


def extract_variables(content: str) -> List[str]:
    """从 GDScript 代码中提取所有变量名"""
    return _GDSCRIPT_VAR_PATTERN.findall(content)


def extract_gdscript_metadata(content: str) -> Dict[str, Any]:
    """从 GDScript 代码中提取元数据

    Args:
        content: GDScript 代码内容

    Returns:
        元数据字典，包含 class_name, extends, functions, signals, variables
    """
    return {
        "class_name": extract_class_name(content),
        "extends": extract_extends(content),
        "functions": extract_functions(content),
        "signals": extract_signals(content),
        "variables": extract_variables(content),
    }
