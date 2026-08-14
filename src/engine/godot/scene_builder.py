"""GameForge - Godot 场景构建器

将场景描述 JSON 转换为 Godot .tscn 文件格式。
支持 Godot 3.x 和 4.x 的场景格式差异。
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()


# Godot 节点类型映射 — 从通用角色到 Godot 类型
ROLE_TO_GODOT_TYPE = {
    # 2D 节点
    "player": "CharacterBody2D",
    "enemy": "CharacterBody2D",
    "npc": "CharacterBody2D",
    "ground": "StaticBody2D",
    "wall": "StaticBody2D",
    "platform": "StaticBody2D",
    "obstacle": "StaticBody2D",
    "coin": "Area2D",
    "pickup": "Area2D",
    "trigger": "Area2D",
    "hazard": "Area2D",
    "projectile": "RigidBody2D",
    "physics_object": "RigidBody2D",
    "camera": "Camera2D",
    "ui": "CanvasLayer",
    "light": "PointLight2D",
    "sprite": "Sprite2D",
    "animated_sprite": "AnimatedSprite2D",
    "label": "Label",
    "tilemap": "TileMap",
    # 3D 节点
    "player_3d": "CharacterBody3D",
    "enemy_3d": "CharacterBody3D",
    "ground_3d": "StaticBody3D",
    "camera_3d": "Camera3D",
    "light_3d": "DirectionalLight3D",
    "mesh": "MeshInstance3D",
    "rigid_body": "RigidBody3D",
    # 通用
    "spawner": "Node2D",
    "manager": "Node",
    "container": "Node2D",
    "root": "Node2D",
}

# Godot 原始形状节点
PRIMITIVE_TO_GODOT = {
    "Cube": "MeshInstance3D",
    "Sphere": "MeshInstance3D",
    "Capsule": "MeshInstance3D",
    "Cylinder": "MeshInstance3D",
    "Plane": "MeshInstance3D",
    "Quad": "MeshInstance3D",
}


class GodotSceneBuilder:
    """Godot 场景构建器

    将场景描述 JSON 转换为 .tscn 文件内容。
    """

    def __init__(self, godot_version: int = 4):
        self.godot_version = godot_version
        self._node_counter = 0

    def build_tscn(self, scene_desc: Dict[str, Any]) -> str:
        """将场景描述转换为 .tscn 文件内容

        Args:
            scene_desc: 场景描述 JSON

        Returns:
            .tscn 文件内容
        """
        self._node_counter = 0
        scene_name = scene_desc.get("scene_name", "GameScene")
        is_2d = self._detect_dimension(scene_desc)

        lines = [
            f'[gd_scene load_steps=2 format=3]',
            f'',
            f'[ext_resource type="Script" path="res://scripts/game_manager.gd" id="1"]',
            f'',
        ]

        # 根节点
        root_type = "Node2D" if is_2d else "Node3D"
        lines.append(f'[node name="{scene_name}" type="{root_type}"]')
        lines.append('script = ExtResource("1")')
        lines.append('')

        # 相机
        camera = scene_desc.get("camera", {})
        if camera:
            lines.extend(self._build_camera(camera, is_2d))
            lines.append('')

        # 光照
        lighting = scene_desc.get("lighting", {})
        if lighting:
            lines.extend(self._build_light(lighting, is_2d))
            lines.append('')

        # 游戏对象
        game_objects = scene_desc.get("game_objects", [])
        for obj in game_objects:
            node_lines = self._build_game_object(obj, is_2d)
            lines.extend(node_lines)
            lines.append('')

        return "\n".join(lines)

    def _detect_dimension(self, scene_desc: Dict[str, Any]) -> bool:
        """检测是 2D 还是 3D 场景"""
        camera = scene_desc.get("camera", {})
        if camera.get("orthographic", False):
            return True
        if camera.get("type", "").endswith("2D"):
            return True

        # 检查游戏对象类型
        for obj in scene_desc.get("game_objects", []):
            obj_type = obj.get("type", "")
            if obj_type in ("Sprite", "Sprite2D", "AnimatedSprite2D", "TileMap"):
                return True
            for comp in obj.get("components", []):
                comp_type = comp.get("type", "")
                if "2D" in comp_type:
                    return True

        return False

    def _build_camera(self, camera: Dict[str, Any], is_2d: bool) -> List[str]:
        """构建相机节点"""
        self._node_counter += 1
        camera_type = "Camera2D" if is_2d else "Camera3D"
        pos = camera.get("position", [0, 0, 5])

        lines = [
            f'[node name="Camera" type="{camera_type}" parent="."]',
        ]

        if is_2d:
            if camera.get("orthographic", True):
                lines.append(f'zoom = Vector2({camera.get("orthographic_size", 1)}, {camera.get("orthographic_size", 1)})')
            lines.append(f'position = Vector2({pos[0]}, {pos[1]})')
        else:
            lines.append(f'transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {pos[0]}, {pos[1]}, {pos[2]})')

        return lines

    def _build_light(self, lighting: Dict[str, Any], is_2d: bool) -> List[str]:
        """构建光照节点"""
        light_type = lighting.get("type", "directional")
        intensity = lighting.get("intensity", 1.0)
        rotation = lighting.get("rotation", [0, 0, 0])

        if is_2d:
            lines = [
                f'[node name="Light" type="DirectionalLight2D" parent="."]',
                f'energy = {intensity}',
            ]
        else:
            godot_light_type = "DirectionalLight3D" if light_type == "directional" else "OmniLight3D"
            lines = [
                f'[node name="Light" type="{godot_light_type}" parent="."]',
                f'light_energy = {intensity}',
                f'rotation_degrees = Vector3({rotation[0]}, {rotation[1]}, {rotation[2]})',
            ]

        return lines

    def _build_game_object(self, obj: Dict[str, Any], is_2d: bool) -> List[str]:
        """构建游戏对象节点"""
        name = obj.get("name", f"Object_{self._node_counter}")
        self._node_counter += 1

        # 确定节点类型
        obj_type = obj.get("type", "")
        role = obj.get("role", "")
        node_type = self._resolve_node_type(obj_type, role, is_2d)

        # 位置/旋转/缩放
        pos = obj.get("position", [0, 0, 0])
        rotation = obj.get("rotation", [0, 0, 0])
        scale = obj.get("scale", [1, 1, 1])

        lines = [
            f'[node name="{name}" type="{node_type}" parent="."]',
        ]

        # 变换
        if is_2d:
            lines.append(f'position = Vector2({pos[0]}, {pos[1]})')
            if rotation[2] != 0:
                lines.append(f'rotation_degrees = {rotation[2]}')
            if scale[0] != 1 or scale[1] != 1:
                lines.append(f'scale = Vector2({scale[0]}, {scale[1]})')
        else:
            lines.append(f'transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {pos[0]}, {pos[1]}, {pos[2]})')

        # 碰撞形状
        for comp in obj.get("components", []):
            comp_type = comp.get("type", "")
            if "Collider" in comp_type or "Collision" in comp_type:
                shape_lines = self._build_collision_shape(comp, is_2d)
                lines.extend(shape_lines)

        # 脚本组件
        for comp in obj.get("components", []):
            comp_type = comp.get("type", "")
            if comp_type and "Collider" not in comp_type and "Collision" not in comp_type:
                script_path = f"res://scripts/{comp_type.lower()}.gd"  # 组件类型转小写即脚本名（str 无 to_snake_case）
                lines.append(f'[node name="{comp_type}" type="Node" parent="{name}"]')
                lines.append(f'script = ExtResource("{script_path}")')

        return lines

    def _resolve_node_type(self, obj_type: str, role: str, is_2d: bool) -> str:
        """解析节点类型"""
        # 优先使用角色映射
        if role and role.lower() in ROLE_TO_GODOT_TYPE:
            return ROLE_TO_GODOT_TYPE[role.lower()]

        # 使用类型映射
        if obj_type in PRIMITIVE_TO_GODOT:
            return PRIMITIVE_TO_GODOT[obj_type]

        # 类型名称直接匹配
        type_lower = obj_type.lower()
        if "2d" in type_lower:
            return obj_type
        if "3d" in type_lower:
            return obj_type

        # 默认
        if is_2d:
            return "Node2D"
        return "Node3D"

    def _build_collision_shape(self, comp: Dict[str, Any], is_2d: bool) -> List[str]:
        """构建碰撞形状"""
        comp_type = comp.get("type", "")
        props = comp.get("properties", {})

        shape_type = "CircleShape2D" if is_2d else "SphereShape3D"
        if "Box" in comp_type:
            shape_type = "RectangleShape2D" if is_2d else "BoxShape3D"

        lines = [
            f'[node name="CollisionShape" type="CollisionShape2D" parent="."]' if is_2d else
            f'[node name="CollisionShape" type="CollisionShape3D" parent="."]',
        ]

        size = props.get("size", "[1, 1]")
        if isinstance(size, str):
            size = size.strip("[]").split(",")
            size = [float(s.strip()) for s in size]

        if is_2d:
            lines.append(f'shape = SubResource("shape_{self._node_counter}")')
        else:
            lines.append(f'shape = SubResource("shape_{self._node_counter}")')

        return lines
