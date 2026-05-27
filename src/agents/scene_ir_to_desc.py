"""Scene IR → scene_description.json 确定性转换。

根据 genre、layout、camera mode 规则性地生成完整的 Unity 场景描述，
包括坐标计算、组件绑定、颜色选择、灯光设置。
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from src.agents.scene_ir import SceneIR, EntityIR, SceneDescription, SceneObject, ComponentSpec, CameraSpec, LightingSpec


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════

def ir_to_scene_description(
    ir: SceneIR,
    file_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """将 SceneIR 确定性转换为 scene_description JSON dict。

    Args:
        ir: 场景中间表示
        file_metadata: 代码文件元数据（class_name → required_components 等）

    Returns:
        符合 scene_description.json 格式的 dict
    """
    file_metadata = file_metadata or {}
    palette = _get_palette(ir.theme, ir.genre)
    objects: List[SceneObject] = []

    # 全局实体索引，用于坐标计算
    entity_index = 0
    total_entities = sum(e.count for e in ir.entities)

    for entity in ir.entities:
        positions = _compute_positions(
            entity, entity_index, total_entities, ir.layout, ir.camera.mode
        )
        for i in range(entity.count):
            pos = positions[i] if i < len(positions) else positions[-1]
            obj = _build_scene_object(
                entity, i, pos, palette, file_metadata, ir.camera.mode,
            )
            objects.append(obj)
            entity_index += 1

    # 相机
    cam = _build_camera(ir.camera, ir.genre)

    # 灯光
    lighting = _build_lighting(ir.genre)

    scene_desc = SceneDescription(
        scene_name=ir.scene_name,
        new_scene=True,
        camera=cam,
        lighting=lighting,
        game_objects=objects,
    )
    return scene_desc.model_dump(exclude_none=True)


# ═══════════════════════════════════════════════════════════════
#  坐标计算
# ═══════════════════════════════════════════════════════════════

def _compute_positions(
    entity: EntityIR,
    global_index: int,
    total: int,
    layout: str,
    camera_mode: str,
) -> List[Tuple[float, float, float]]:
    """根据 layout 和 spawn_zone 计算每个实例的世界坐标。"""
    is_2d = "2d" in camera_mode or "side" in camera_mode
    positions = []

    for i in range(entity.count):
        idx = global_index + i

        if layout == "linear":
            x = (idx - total / 2) * 3.0
            y = _zone_y(entity.spawn_zone, is_2d)
            z = 0 if is_2d else 0
        elif layout == "arena":
            angle = (2 * math.pi * idx) / max(total, 1)
            radius = 5.0
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius if not is_2d else _zone_y(entity.spawn_zone, is_2d)
            z = math.sin(angle) * radius if is_2d else 0
        elif layout == "grid":
            cols = max(1, int(math.ceil(math.sqrt(total))))
            row = idx // cols
            col = idx % cols
            x = (col - cols / 2) * 3.0
            y = _zone_y(entity.spawn_zone, is_2d)
            z = row * 3.0 if not is_2d else 0
        else:  # room_based / open_world
            x = _zone_x(entity.spawn_zone) + i * 2.0
            y = _zone_y(entity.spawn_zone, is_2d)
            z = 0 if is_2d else i * 2.0

        positions.append((round(x, 2), round(y, 2), round(z, 2)))

    return positions


def _zone_x(zone: str) -> float:
    return {"left": -6, "right": 6, "center": 0, "top": 0, "bottom": 0, "random": 0}.get(zone, 0)


def _zone_y(zone: str, is_2d: bool) -> float:
    if is_2d:
        return {"bottom": -3, "top": 3, "center": 0, "left": 0, "right": 0, "random": 1}.get(zone, 0)
    return 0


# ═══════════════════════════════════════════════════════════════
#  场景对象构建
# ═══════════════════════════════════════════════════════════════

_ROLE_TO_TYPE: Dict[str, str] = {
    "player": "Sprite",
    "ground": "Cube",
    "platform": "Cube",
    "obstacle": "Cube",
    "enemy": "Sprite",
    "npc": "Sprite",
    "pickup": "Sprite",
    "spawner": "Empty",
    "manager": "Empty",
    "decoration": "Cube",
    "boundary": "Empty",
    "camera": "Camera",
}

_ROLE_TO_SCALE: Dict[str, List[float]] = {
    "player": [1, 1, 1],
    "ground": [20, 0.5, 1],
    "platform": [4, 0.5, 1],
    "obstacle": [1, 1, 1],
    "enemy": [1, 1, 1],
    "npc": [1, 1, 1],
    "pickup": [0.5, 0.5, 0.5],
    "spawner": [1, 1, 1],
    "manager": [1, 1, 1],
    "decoration": [1, 2, 1],
    "boundary": [1, 1, 1],
    "camera": [1, 1, 1],
}

_ROLE_TO_COMPONENTS: Dict[str, List[str]] = {
    "player": ["Rigidbody2D", "BoxCollider2D"],
    "ground": ["BoxCollider2D"],
    "platform": ["BoxCollider2D"],
    "obstacle": ["BoxCollider2D"],
    "enemy": ["Rigidbody2D", "BoxCollider2D"],
    "npc": ["BoxCollider2D"],
    "pickup": ["BoxCollider2D"],
    "spawner": [],
    "manager": [],
    "decoration": [],
    "boundary": ["BoxCollider2D"],
    "camera": [],
}

_ROLE_TO_TAG: Dict[str, str] = {
    "player": "Player",
    "enemy": "Untagged",
    "pickup": "Untagged",
}

_ROLE_TO_COLOR: Dict[str, str] = {
    "player": "player_blue",
    "ground": "ground_brown",
    "platform": "platform_gray",
    "obstacle": "obstacle_red",
    "enemy": "enemy_red",
    "npc": "npc_green",
    "pickup": "coin_gold",
    "decoration": "tree_green",
}


def _build_scene_object(
    entity: EntityIR,
    instance_idx: int,
    position: Tuple[float, float, float],
    palette: Dict[str, List[float]],
    file_metadata: Dict[str, Any],
    camera_mode: str,
) -> SceneObject:
    name = entity.name if entity.count == 1 else f"{entity.name}{instance_idx + 1}"
    obj_type = _ROLE_TO_TYPE.get(entity.role, "Empty")
    scale = list(_ROLE_TO_SCALE.get(entity.role, [1, 1, 1]))

    # 地面在 2D 模式下用 Sprite
    if entity.role == "ground" and "2d" in camera_mode:
        obj_type = "Sprite"
        scale = [20, 1, 1]

    # 组件
    components: List[ComponentSpec] = []
    for comp_type in _ROLE_TO_COMPONENTS.get(entity.role, []):
        props: Dict[str, str] = {}
        if comp_type == "Rigidbody2D":
            props = {"gravityScale": "3" if entity.role == "player" else "1", "mass": "1"}
            if entity.role in ("pickup", "decoration"):
                props["bodyType"] = "Static"
        elif comp_type == "BoxCollider2D":
            if entity.role == "ground":
                props = {"size": "20,1"}
            elif entity.role == "pickup":
                props = {"isTrigger": "true"}
        components.append(ComponentSpec(type=comp_type, properties=props))

    # 自定义脚本
    if entity.script:
        components.append(ComponentSpec(type=entity.script, properties={}))

    # 颜色
    color_key = _ROLE_TO_COLOR.get(entity.role)
    color = list(palette.get(color_key, palette.get("default", [1, 1, 1, 1])))

    # Tag
    tag = _ROLE_TO_TAG.get(entity.role, "")

    return SceneObject(
        name=name,
        type=obj_type,
        position=list(position),
        rotation=[0, 0, 0],
        scale=scale,
        tag=tag,
        layer=0,
        is_static=entity.role in ("ground", "platform", "decoration", "boundary"),
        color=color,
        components=components,
    )


# ═══════════════════════════════════════════════════════════════
#  相机 / 灯光
# ═══════════════════════════════════════════════════════════════

def _build_camera(cam_ir, genre: str) -> CameraSpec:
    bg = _BACKGROUND_COLORS.get(cam_ir.background, [0.5, 0.8, 1.0, 1.0])
    if cam_ir.mode in ("2d_side_view", "top_down"):
        pos = [0, 0, -10] if cam_ir.mode == "2d_side_view" else [0, 10, 0]
        return CameraSpec(
            position=pos,
            orthographic=True,
            orthographic_size=6.0,
            background_color=bg,
        )
    return CameraSpec(
        position=[0, 2, -8],
        orthographic=False,
        orthographic_size=6.0,
        background_color=bg,
    )


def _build_lighting(genre: str) -> LightingSpec:
    return LightingSpec(type="directional", intensity=1.0, rotation=[50, -30, 0])


# ═══════════════════════════════════════════════════════════════
#  调色板
# ═══════════════════════════════════════════════════════════════

_PALETTES: Dict[str, Dict[str, List[float]]] = {
    "default": {
        "player_blue": [0.2, 0.5, 1.0, 1.0],
        "ground_brown": [0.4, 0.26, 0.13, 1.0],
        "platform_gray": [0.6, 0.6, 0.6, 1.0],
        "obstacle_red": [0.8, 0.2, 0.2, 1.0],
        "enemy_red": [0.9, 0.1, 0.1, 1.0],
        "npc_green": [0.2, 0.8, 0.3, 1.0],
        "coin_gold": [1.0, 0.84, 0.0, 1.0],
        "tree_green": [0.13, 0.55, 0.13, 1.0],
        "default": [0.7, 0.7, 0.7, 1.0],
    },
    "space": {
        "player_blue": [0.3, 0.7, 1.0, 1.0],
        "ground_brown": [0.2, 0.2, 0.3, 1.0],
        "platform_gray": [0.4, 0.4, 0.5, 1.0],
        "obstacle_red": [1.0, 0.3, 0.3, 1.0],
        "enemy_red": [1.0, 0.0, 0.0, 1.0],
        "npc_green": [0.0, 1.0, 0.5, 1.0],
        "coin_gold": [1.0, 1.0, 0.5, 1.0],
        "tree_green": [0.0, 0.8, 0.8, 1.0],
        "default": [0.5, 0.5, 0.6, 1.0],
    },
    "forest": {
        "player_blue": [0.2, 0.4, 0.9, 1.0],
        "ground_brown": [0.36, 0.25, 0.15, 1.0],
        "platform_gray": [0.5, 0.5, 0.4, 1.0],
        "obstacle_red": [0.7, 0.3, 0.2, 1.0],
        "enemy_red": [0.8, 0.15, 0.1, 1.0],
        "npc_green": [0.3, 0.7, 0.2, 1.0],
        "coin_gold": [1.0, 0.85, 0.2, 1.0],
        "tree_green": [0.1, 0.5, 0.1, 1.0],
        "default": [0.4, 0.6, 0.3, 1.0],
    },
}

_BACKGROUND_COLORS: Dict[str, List[float]] = {
    "sky_blue": [0.53, 0.81, 0.92, 1.0],
    "space_black": [0.05, 0.05, 0.1, 1.0],
    "forest_green": [0.15, 0.35, 0.15, 1.0],
    "dungeon_dark": [0.1, 0.08, 0.12, 1.0],
    "sunset_orange": [1.0, 0.6, 0.3, 1.0],
    "warm_beige": [0.96, 0.9, 0.8, 1.0],
}


def _get_palette(theme: Optional[str], genre: str) -> Dict[str, List[float]]:
    if theme:
        theme_lower = theme.lower()
        if any(w in theme_lower for w in ["太空", "space", "宇宙"]):
            return _PALETTES["space"]
        if any(w in theme_lower for w in ["森林", "forest", "自然", "草"]):
            return _PALETTES["forest"]

    if genre == "shooter":
        return _PALETTES["space"]
    if genre in ("rpg", "runner"):
        return _PALETTES["forest"]
    return _PALETTES["default"]
