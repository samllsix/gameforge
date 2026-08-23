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

    将场景描述 JSON 转换为 Godot .tscn 文件内容。
    """

    def __init__(self, godot_version: int = 4):
        self.godot_version = godot_version
        self._node_counter = 0
        self._ext_resources: List[Dict[str, str]] = []
        self._sub_resources: List[Dict[str, Any]] = []
        self._ext_res_counter = 0
        self._sub_res_counter = 0

    def _add_ext_resource(self, path: str, res_type: str = "Script") -> str:
        """注册一个外部资源，返回其 ID（如已存在则复用）。"""
        for r in self._ext_resources:
            if r["path"] == path:
                return r["id"]
        self._ext_res_counter += 1
        res_id = str(self._ext_res_counter)
        self._ext_resources.append({"path": path, "type": res_type, "id": res_id})
        return res_id

    def _add_sub_resource(self, res_type: str, properties: Dict[str, Any]) -> str:
        """注册一个子资源，返回其 ID。相同类型+属性的子资源自动去重。"""
        prop_key = json.dumps(properties, sort_keys=True, default=str)
        dedup_key = f"{res_type}:{prop_key}"
        if hasattr(self, '_sub_res_dedup') and dedup_key in self._sub_res_dedup:
            return self._sub_res_dedup[dedup_key]
        if not hasattr(self, '_sub_res_dedup'):
            self._sub_res_dedup = {}
        self._sub_res_counter += 1
        sub_id = f"sub_{self._sub_res_counter}"
        self._sub_resources.append({"type": res_type, "id": sub_id, "properties": properties})
        ref = f"SubResource(\"{sub_id}\")"
        self._sub_res_dedup[dedup_key] = ref
        return ref

    def build_tscn(self, scene_desc: Dict[str, Any]) -> str:
        """将场景描述转换为 .tscn 文件内容

        Args:
            scene_desc: 场景描述 JSON

        Returns:
            .tscn 文件内容
        """
        self._node_counter = 0
        self._ext_resources = []
        self._sub_resources = []
        self._ext_res_counter = 0
        self._sub_res_counter = 0
        self._sub_res_dedup = {}

        scene_name = scene_desc.get("scene_name", "GameScene")
        is_2d = self._detect_dimension(scene_desc)

        # 构建节点行（同时收集 ext_resource 和 sub_resource）
        node_lines: List[str] = []

        # 根节点
        root_type = "Node2D" if is_2d else "Node3D"
        node_lines.append(f'[node name="{scene_name}" type="{root_type}"]')
        node_lines.append('')

        # 背景色
        camera = scene_desc.get("camera", {})
        bg_color = camera.get("background_color")
        bg_size = camera.get("viewport_size") or [640, 360]
        if bg_color and is_2d:
            node_lines.extend(self._build_background(bg_color, bg_size))
            node_lines.append('')

        # 相机
        if camera:
            node_lines.extend(self._build_camera(camera, is_2d))
            node_lines.append('')

        # 光照
        lighting = scene_desc.get("lighting", {})
        if lighting:
            node_lines.extend(self._build_light(lighting, is_2d))
            node_lines.append('')

        # 游戏对象
        for obj in scene_desc.get("game_objects", []):
            node_lines.extend(self._build_game_object(obj, is_2d))
            node_lines.append('')

        # 组装最终输出：header → ext_resource → sub_resource → node
        load_steps = 1 + len(self._ext_resources) + len(self._sub_resources)

        lines = [f'[gd_scene load_steps={load_steps} format=3]', '']

        for r in self._ext_resources:
            lines.append(f'[ext_resource type="{r["type"]}" path="{r["path"]}" id="{r["id"]}"]')
        if self._ext_resources:
            lines.append('')

        for s in self._sub_resources:
            lines.append(f'[sub_resource type="{s["type"]}" id="{s["id"]}"]')
            for k, v in s["properties"].items():
                lines.append(f'{k} = {self._format_value(v)}')
            lines.append('')

        lines.extend(node_lines)

        return "\n".join(lines)

    @staticmethod
    def _component_to_script_name(comp_type: str) -> str:
        """将组件类型转换为 GDScript 文件名（snake_case），去除 Controller 后缀。"""
        import re
        if comp_type.endswith("Controller"):
            comp_type = comp_type[:-len("Controller")]
        s = re.sub(r'(?<!^)(?=[A-Z])', '_', comp_type).lower()
        s = s.replace("__", "_").strip("_")
        return s if s else ""

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            if value.startswith("SubResource") or value.startswith("ExtResource"):
                return value
            _UNQUOTED_PREFIXES = ("Color(", "Vector2(", "Vector3(", "Transform3D(")
            if any(value.startswith(p) for p in _UNQUOTED_PREFIXES):
                return value
            return f'"{value}"'
        elif isinstance(value, (list, tuple)):
            if len(value) == 2:
                return f'Vector2({value[0]}, {value[1]})'
            elif len(value) == 3:
                return f'Vector3({value[0]}, {value[1]}, {value[2]})'
            return str(list(value))
        return str(value)

    @staticmethod
    def _resolve_shape_type(comp_type: str, is_2d: bool) -> str:
        if "Box" in comp_type:
            return "RectangleShape2D" if is_2d else "BoxShape3D"
        if "Circle" in comp_type or "Sphere" in comp_type:
            return "CircleShape2D" if is_2d else "SphereShape3D"
        if "Capsule" in comp_type:
            return "CapsuleShape2D" if is_2d else "CapsuleShape3D"
        return "RectangleShape2D" if is_2d else "BoxShape3D"

    def _build_shape_properties(self, comp: Dict[str, Any], is_2d: bool,
                                shape_type: str = "") -> Dict[str, Any]:
        """构建碰撞形状的属性 — 根据形状类型返回正确的属性名。"""
        props = comp.get("properties", {})
        size = props.get("size", [1, 1])
        if isinstance(size, str):
            size = size.strip("[]").split(",")
            size = [float(s.strip()) for s in size]

        w = float(size[0]) if len(size) > 0 else 1.0
        h = float(size[1]) if len(size) > 1 else 1.0

        if shape_type in ("CircleShape2D", "SphereShape3D"):
            return {"radius": w / 2}
        if shape_type in ("CapsuleShape2D", "CapsuleShape3D"):
            return {"radius": w / 2, "height": h}
        if is_2d:
            return {"size": (w, h)}
        d = float(size[2]) if len(size) > 2 else 1.0
        return {"size": (w, h, d)}

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

    def _build_background(self, bg_color: List[float], viewport_size: List[float] = None) -> List[str]:
        """构建 2D 背景色节点 — ColorRect（2D 原生 CanvasItem）。

        之前的实现使用 MeshInstance2D + StandardMaterial3D + QuadMesh，
        是 Godot 4 渲染管线的非法组合，会导致场景加载失败。
        这里改用 ColorRect：纯 CanvasItem，无 3D 材质依赖。

        注意：ColorRect 的 anchor_* 属性只在父节点是 Control 时生效；
        我们的父是 Node2D，所以 anchor_* 被忽略。
        必须显式给 size：把 viewport 整个铺满。
        """
        r, g, b = bg_color[0], bg_color[1], bg_color[2]
        a = bg_color[3] if len(bg_color) > 3 else 1.0
        w = float(viewport_size[0]) if viewport_size and len(viewport_size) > 0 else 640.0
        h = float(viewport_size[1]) if viewport_size and len(viewport_size) > 1 else 360.0
        # 默认 anchor_*=0 让 offset_* 决定矩形位置；
        # 负的 offset_left/top 让矩形覆盖 (0,0)..(w,h)，中心对齐
        return [
            '[node name="Background" type="ColorRect" parent="."]',
            'anchor_left = 0.0',
            'anchor_top = 0.0',
            'anchor_right = 0.0',
            'anchor_bottom = 0.0',
            f'offset_left = {0 - w / 2}',
            f'offset_top = {0 - h / 2}',
            f'offset_right = {w / 2}',
            f'offset_bottom = {h / 2}',
            f'color = Color({r}, {g}, {b}, {a})',
            'z_index = -100',
            'mouse_filter = 2',
        ]

    def _build_camera(self, camera: Dict[str, Any], is_2d: bool) -> List[str]:
        """构建相机节点"""
        self._node_counter += 1
        camera_type = "Camera2D" if is_2d else "Camera3D"
        pos = camera.get("position", [0, 0, 5])

        lines = [
            f'[node name="Camera" type="{camera_type}" parent="."]',
        ]

        if is_2d:
            ortho_size = camera.get("orthographic_size", 5)
            zoom_val = 1.0 / max(ortho_size, 0.1)
            zoom_val = max(0.3, min(zoom_val, 2.0))
            lines.append(f'zoom = Vector2({zoom_val}, {zoom_val})')
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

        # 变换 — Node 类型不支持 position/transform 属性
        # 注意：不在父节点上设置 scale，因为子节点（CollisionShape/Mesh）已自带正确尺寸。
        # 若父节点有 scale，子节点的尺寸会被二次放大，导致碰撞体和视觉变大 N倍。
        _NO_TRANSFORM_TYPES = {"Node"}
        if node_type not in _NO_TRANSFORM_TYPES:
            if is_2d:
                lines.append(f'position = Vector2({pos[0]}, {pos[1]})')
                if rotation[2] != 0:
                    lines.append(f'rotation_degrees = {rotation[2]}')
            else:
                lines.append(f'transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {pos[0]}, {pos[1]}, {pos[2]})')

        # 脚本组件：直接挂载到当前节点
        _IGNORED_COMPONENTS = {"rigidbody2d", "rigidbody", "rigidbody3d",
                                "charactercontroller", "transform", "audio_source",
                                "mesh_renderer", "mesh_filter", "sprite_renderer",
                                "characterbody2d", "characterbody3d", "staticbody2d",
                                "staticbody3d", "area2d", "area3d", "rigidbody2d",
                                "camera2d", "camera3d", "node2d", "node3d", "node",
                                "collisionShape2d", "collisionshape3d", "sprite2d",
                                "animatedsprite2d", "tilemap", "canvlasayer",
                                "directionallight2d", "directionallight3d",
                                "pointlight2d", "pointlight3d", "label"}
        for comp in obj.get("components", []):
            comp_type = comp.get("type", "")
            if not comp_type:
                continue
            if "Collider" in comp_type or "Collision" in comp_type:
                continue
            if comp_type.lower() in _IGNORED_COMPONENTS:
                continue
            script_name = self._component_to_script_name(comp_type)
            if script_name:
                script_path = f"res://scripts/{script_name}.gd"
                res_id = self._add_ext_resource(script_path, "Script")
                lines.append(f'script = ExtResource("{res_id}")')
                break

        # 碰撞形状：作为子节点
        for comp in obj.get("components", []):
            comp_type = comp.get("type", "")
            if "Collider" in comp_type or "Collision" in comp_type:
                shape_lines = self._build_collision_shape(comp, name, is_2d)
                lines.extend(shape_lines)

        # 可视化：为有 color 的对象添加 MeshInstance2D 子节点
        color = obj.get("color")
        if color and node_type not in _NO_TRANSFORM_TYPES:
            lines.extend(self._build_visual_node(name, color, scale, is_2d))

        return lines

    def _build_visual_node(self, parent_name: str, color: List[float],
                           scale: List[float], is_2d: bool) -> List[str]:
        """构建可视化子节点。

        - 2D：用 ColorRect（CanvasItem，纯 2D 原生节点，无 3D 材质依赖）。
          之前的 MeshInstance2D + StandardMaterial3D + QuadMesh 是 Godot 4 渲染管线的
          非法组合，会导致场景加载/渲染异常（详见 P0-1）。
        - 3D：用 MeshInstance3D + BoxMesh（合法 3D 用法，保持不变）。
        """
        if is_2d:
            w = abs(float(scale[0])) if len(scale) > 0 else 1.0
            h = abs(float(scale[1])) if len(scale) > 1 else 1.0
            if w < 0.05:
                w = 0.05
            if h < 0.05:
                h = 0.05

            r, g, b = color[0], color[1], color[2]
            a = color[3] if len(color) > 3 else 1.0
            return [
                f'[node name="Mesh" type="ColorRect" parent="{parent_name}"]',
                'anchor_left = 0.0',
                'anchor_top = 0.0',
                'anchor_right = 0.0',
                'anchor_bottom = 0.0',
                f'offset_left = -{w / 2}',
                f'offset_top = -{h / 2}',
                f'offset_right = {w / 2}',
                f'offset_bottom = {h / 2}',
                f'color = Color({r}, {g}, {b}, {a})',
                'mouse_filter = 2',
            ]
        else:
            sx = abs(float(scale[0])) if len(scale) > 0 else 1.0
            sy = abs(float(scale[1])) if len(scale) > 1 else 1.0
            sz = abs(float(scale[2])) if len(scale) > 2 else 1.0
            mat_ref = self._add_sub_resource("StandardMaterial3D", {
                "albedo_color": f"Color({color[0]}, {color[1]}, {color[2]}, {color[3] if len(color) > 3 else 1.0})",
            })
            mesh_props = {
                "size": f"Vector3({sx}, {sy}, {sz})",
                "material": mat_ref,
            }
            mesh_ref = self._add_sub_resource("BoxMesh", mesh_props)
            return [
                f'[node name="Mesh" type="MeshInstance3D" parent="{parent_name}"]',
                f'mesh = {mesh_ref}',
            ]

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

    def _build_collision_shape(self, comp: Dict[str, Any], parent_name: str, is_2d: bool) -> List[str]:
        """构建碰撞形状节点（作为 parent_name 的子节点）。"""
        comp_type = comp.get("type", "")
        shape_type = self._resolve_shape_type(comp_type, is_2d)
        props = self._build_shape_properties(comp, is_2d, shape_type)
        shape_ref = self._add_sub_resource(shape_type, props)

        collision_type = "CollisionShape2D" if is_2d else "CollisionShape3D"
        lines = [
            f'[node name="CollisionShape" type="{collision_type}" parent="{parent_name}"]',
            f'shape = {shape_ref}',
        ]
        return lines
