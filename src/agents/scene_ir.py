"""Scene IR（中间表示）— 场景的抽象描述，介于 GDM 和 scene_description.json 之间。

LLM 只需输出 Scene IR（少量字段、抽象语义），由后端确定性转换为 Unity 场景 JSON。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
#  Scene IR — LLM 输出的目标格式
# ═══════════════════════════════════════════════════════════════

_VALID_GENRES = {"platformer", "shooter", "rpg", "puzzle", "runner", "tower_defense"}
_VALID_LAYOUTS = {"linear", "arena", "open_world", "grid", "room_based"}
_VALID_DIFFICULTIES = {"easy", "medium", "hard"}
_VALID_ROLES = {
    "player", "ground", "platform", "obstacle", "enemy", "npc",
    "pickup", "spawner", "manager", "decoration", "boundary", "camera",
}
_VALID_CAMERA_MODES = {"2d_side_view", "top_down", "3d_third_person", "3d_first_person"}
_VALID_SPAWN_ZONES = {"center", "top", "bottom", "left", "right", "random"}


class CameraIR(BaseModel):
    mode: str = "2d_side_view"
    follow_target: Optional[str] = "Player"
    background: str = "sky_blue"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in _VALID_CAMERA_MODES:
            return "2d_side_view"
        return v


class EntityIR(BaseModel):
    name: str
    role: str
    count: int = 1
    spawn_zone: str = "center"
    script: Optional[str] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            return "decoration"
        return v

    @field_validator("count")
    @classmethod
    def validate_count(cls, v: int) -> int:
        return max(1, v)

    @field_validator("spawn_zone")
    @classmethod
    def validate_spawn_zone(cls, v: str) -> str:
        if v not in _VALID_SPAWN_ZONES:
            return "center"
        return v


class SceneIR(BaseModel):
    scene_name: str = "GameScene"
    genre: str = "platformer"
    layout: str = "linear"
    difficulty: str = "easy"
    camera: CameraIR = Field(default_factory=CameraIR)
    entities: List[EntityIR] = Field(default_factory=list)
    theme: Optional[str] = None

    @field_validator("genre")
    @classmethod
    def validate_genre(cls, v: str) -> str:
        if v not in _VALID_GENRES:
            return "platformer"
        return v

    @field_validator("layout")
    @classmethod
    def validate_layout(cls, v: str) -> str:
        if v not in _VALID_LAYOUTS:
            return "linear"
        return v

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, v: str) -> str:
        if v not in _VALID_DIFFICULTIES:
            return "easy"
        return v


# ═══════════════════════════════════════════════════════════════
#  Scene Description — 最终输出的 schema 校验模型
# ═══════════════════════════════════════════════════════════════

class ComponentSpec(BaseModel):
    type: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class CameraSpec(BaseModel):
    position: List[float] = Field(default_factory=lambda: [0, 1, -10])
    orthographic: bool = True
    orthographic_size: float = 6.0
    background_color: List[float] = Field(default_factory=lambda: [0.5, 0.8, 1.0, 1.0])


class LightingSpec(BaseModel):
    type: str = "directional"
    intensity: float = 1.0
    rotation: List[float] = Field(default_factory=lambda: [50, -30, 0])


class SceneObject(BaseModel):
    name: str
    type: str = "Empty"
    role: str = ""
    position: List[float] = Field(default_factory=lambda: [0, 0, 0])
    rotation: List[float] = Field(default_factory=lambda: [0, 0, 0])
    scale: List[float] = Field(default_factory=lambda: [1, 1, 1])
    tag: str = ""
    layer: int = 0
    is_static: bool = False
    sprite: Optional[str] = None
    color: Optional[List[float]] = None
    material: Optional[str] = None
    particle_effect: Optional[str] = None
    components: List[ComponentSpec] = Field(default_factory=list)
    children: List["SceneObject"] = Field(default_factory=list)


class SceneDescription(BaseModel):
    scene_name: str = "GameScene"
    new_scene: bool = True
    camera: CameraSpec = Field(default_factory=CameraSpec)
    lighting: LightingSpec = Field(default_factory=LightingSpec)
    game_objects: List[SceneObject] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  从 GDM 推断 Scene IR 的辅助函数
# ═══════════════════════════════════════════════════════════════

_GENRE_KEYWORDS: Dict[str, List[str]] = {
    "platformer": ["platformer", "platform", "跳", "jump", "横版", "平台", "side"],
    "shooter": ["shooter", "shoot", "射击", "bullet", "弹幕", "太空", "space"],
    "rpg": ["rpg", "回合", "turn", "角色扮演", "战斗", "quest", "冒险"],
    "puzzle": ["puzzle", "解谜", "谜题", "消除", "match", "益智"],
    "runner": ["runner", "跑酷", "无尽", "endless", "run", "疾跑"],
    "tower_defense": ["tower_defense", "塔防", "tower", "防御"],
}


def infer_genre_from_gdm(gdm: Dict[str, Any]) -> str:
    """从 GDM 推断游戏类型，用于补全 SceneIR 缺失的 genre 字段。"""
    # 收集所有文本
    text = gdm.get("genre", "")
    for s in gdm.get("scenes", []):
        text += " " + str(s.get("purpose", ""))
    text += " " + gdm.get("core_loop", "")
    text += " " + gdm.get("camera_mode", "")
    text_lower = text.lower()

    best_genre = "platformer"
    best_score = 0
    for genre, keywords in _GENRE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_genre = genre

    return best_genre


def repair_scene_ir(raw: Dict[str, Any], gdm: Dict[str, Any]) -> SceneIR:
    """从原始 dict 创建 SceneIR，自动修复缺失/非法字段。"""
    if not raw.get("genre"):
        raw["genre"] = infer_genre_from_gdm(gdm)
    if not raw.get("scene_name"):
        raw["scene_name"] = "GameScene"
    if not raw.get("layout"):
        raw["layout"] = "linear"
    if not raw.get("difficulty"):
        raw["difficulty"] = "easy"
    if not raw.get("camera"):
        raw["camera"] = {}
    if not isinstance(raw.get("entities"), list):
        raw["entities"] = []

    return SceneIR(**raw)
