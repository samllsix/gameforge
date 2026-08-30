"""场景模板库 — 为常见游戏类型提供预定义的 Scene IR 结构。

模板负责：布局规则、默认实体、相机模式、组件组合。
GDM 负责：实体数量、角色名、脚本绑定、主题颜色。
"""

from typing import Any, Dict, List, Optional
from src.agents.scene_ir import SceneIR, CameraIR, EntityIR


# ═══════════════════════════════════════════════════════════════
#  模板定义
# ═══════════════════════════════════════════════════════════════

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "platformer": {
        "genre": "platformer",
        "layout": "linear",
        "camera": {"mode": "2d_side_view", "follow_target": "Player", "background": "sky_blue"},
        "default_entities": [
            {"name": "Player", "role": "player", "count": 1, "spawn_zone": "left", "script": "PlayerController"},
            {"name": "Ground", "role": "ground", "count": 1, "spawn_zone": "bottom", "script": None},
            {"name": "Platform1", "role": "platform", "count": 1, "spawn_zone": "center", "script": None},
            {"name": "Platform2", "role": "platform", "count": 1, "spawn_zone": "right", "script": None},
            {"name": "Coin", "role": "pickup", "count": 3, "spawn_zone": "random", "script": "CoinController"},
            {"name": "Enemy", "role": "enemy", "count": 2, "spawn_zone": "right", "script": "EnemyController"},
            {"name": "GameManager", "role": "manager", "count": 1, "spawn_zone": "center", "script": "GameManager"},
        ],
    },
    "shooter": {
        "genre": "shooter",
        "layout": "arena",
        "camera": {"mode": "top_down", "follow_target": "Player", "background": "space_black"},
        "default_entities": [
            {"name": "Player", "role": "player", "count": 1, "spawn_zone": "center", "script": "PlayerController"},
            {"name": "Enemy", "role": "enemy", "count": 4, "spawn_zone": "random", "script": "EnemyController"},
            {"name": "Boundary", "role": "boundary", "count": 1, "spawn_zone": "center", "script": None},
            {"name": "EnemySpawner", "role": "spawner", "count": 1, "spawn_zone": "center", "script": "GameManager"},
            {"name": "GameManager", "role": "manager", "count": 1, "spawn_zone": "center", "script": "GameManager"},
        ],
    },
    "rpg": {
        "genre": "rpg",
        "layout": "open_world",
        "camera": {"mode": "top_down", "follow_target": "Player", "background": "forest_green"},
        "default_entities": [
            {"name": "Player", "role": "player", "count": 1, "spawn_zone": "center", "script": "PlayerController"},
            {"name": "Ground", "role": "ground", "count": 1, "spawn_zone": "bottom", "script": None},
            {"name": "NPC", "role": "npc", "count": 2, "spawn_zone": "random", "script": None},
            {"name": "Enemy", "role": "enemy", "count": 3, "spawn_zone": "random", "script": "EnemyController"},
            {"name": "Chest", "role": "pickup", "count": 2, "spawn_zone": "random", "script": "CoinController"},
            {"name": "Decoration", "role": "decoration", "count": 4, "spawn_zone": "random", "script": None},
            {"name": "GameManager", "role": "manager", "count": 1, "spawn_zone": "center", "script": "GameManager"},
        ],
    },
    "puzzle": {
        "genre": "puzzle",
        "layout": "grid",
        "camera": {"mode": "top_down", "follow_target": None, "background": "warm_beige"},
        "default_entities": [
            {"name": "Player", "role": "player", "count": 1, "spawn_zone": "center", "script": "PlayerController"},
            {"name": "Ground", "role": "ground", "count": 1, "spawn_zone": "bottom", "script": None},
            {"name": "PuzzlePiece", "role": "pickup", "count": 5, "spawn_zone": "random", "script": "CoinController"},
            {"name": "Obstacle", "role": "obstacle", "count": 3, "spawn_zone": "random", "script": None},
            {"name": "GameManager", "role": "manager", "count": 1, "spawn_zone": "center", "script": "GameManager"},
        ],
    },
    "runner": {
        "genre": "runner",
        "layout": "linear",
        "camera": {"mode": "2d_side_view", "follow_target": "Player", "background": "sunset_orange"},
        "default_entities": [
            {"name": "Player", "role": "player", "count": 1, "spawn_zone": "left", "script": "PlayerController"},
            {"name": "Ground", "role": "ground", "count": 1, "spawn_zone": "bottom", "script": None},
            {"name": "Obstacle", "role": "obstacle", "count": 5, "spawn_zone": "right", "script": None},
            {"name": "Coin", "role": "pickup", "count": 5, "spawn_zone": "random", "script": "CoinController"},
            {"name": "GameManager", "role": "manager", "count": 1, "spawn_zone": "center", "script": "GameManager"},
        ],
    },
}


# ═══════════════════════════════════════════════════════════════
#  模板匹配
# ═══════════════════════════════════════════════════════════════

_GENRE_KEYWORDS: Dict[str, List[str]] = {
    "platformer": ["platform", "跳", "jump", "横版", "平台", "2d", "side"],
    "shooter": ["shoot", "射击", "bullet", "弹幕", "太空", "space", "战机"],
    "rpg": ["rpg", "回合", "turn", "角色扮演", "战斗", "quest", "冒险"],
    "puzzle": ["puzzle", "解谜", "谜题", "消除", "match", "益智"],
    "runner": ["runner", "跑酷", "无尽", "endless", "run", "疾跑"],
}


def match_template(gdm: Dict[str, Any]) -> Optional[str]:
    """根据 GDM 内容匹配最佳模板，返回模板名或 None。"""
    # 先看 GDM 的 genre 字段
    genre = gdm.get("genre", "").lower()
    for name, keywords in _GENRE_KEYWORDS.items():
        if name in genre:
            return name

    # 再看 scenes / core_loop / requirements 的关键词
    text = ""
    for s in gdm.get("scenes", []):
        text += " " + str(s.get("purpose", ""))
    text += " " + gdm.get("core_loop", "")
    text += " " + gdm.get("camera_mode", "")
    text_lower = text.lower()

    best_name = None
    best_score = 0
    for name, keywords in _GENRE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_name = name

    return best_name if best_score > 0 else None


# ═══════════════════════════════════════════════════════════════
#  模板填充
# ═══════════════════════════════════════════════════════════════

# GDM 实体 components 里常见的是 Godot/Unity 节点类型或碰撞组件名，
# 这些不是脚本类名，不能拿去当 script 绑定；否则下游 scene_builder 会
# 把它们当作内置组件忽略，实体脚本被静默丢弃（玩家/敌人失去控制器）。
_NON_SCRIPT_COMPONENTS = {
    "characterbody2d", "characterbody3d", "staticbody2d", "staticbody3d",
    "rigidbody2d", "rigidbody3d", "rigidbody", "area2d", "area3d",
    "node", "node2d", "node3d", "control", "canvaslayer",
    "camera2d", "camera3d", "collisionshape2d", "collisionshape3d",
    "boxcollision", "circlecollision", "capsulecollision",
    "boxcollider2d", "boxcollider", "circlecollider2d", "circlecollider",
    "capsulecollider2d", "capsulecollider", "spherecollider",
    "sprite2d", "sprite", "animatedsprite2d", "label", "tilemap",
    "transform", "charactercontroller", "audiosource",
    "meshrenderer", "meshfilter", "spriterenderer",
    "directionallight2d", "directionallight3d", "pointlight2d", "pointlight3d",
}


def _script_from_components(components: Any) -> Optional[str]:
    """从 GDM 实体的 components 中挑出像脚本类名的项（跳过节点/碰撞等内置组件）。"""
    if not isinstance(components, list):
        return None
    for c in components:
        if isinstance(c, str) and c.strip():
            if c.strip().lower() in _NON_SCRIPT_COMPONENTS:
                continue
            return c.strip()
    return None


def fill_template(template_name: str, gdm: Dict[str, Any]) -> SceneIR:
    """用模板结构 + GDM 数据填充，返回完整 SceneIR。"""
    tpl = TEMPLATES.get(template_name)
    if not tpl:
        tpl = TEMPLATES["platformer"]

    # 从 GDM 提取实体映射（name → script）
    gdm_entity_map: Dict[str, str] = {}
    for ent in gdm.get("entities", []):
        name = ent.get("name", "")
        components = ent.get("components", [])
        # 取第一个"像脚本类名"的组件作为脚本名（跳过 CharacterBody2D 等节点类型）
        script = _script_from_components(components)
        if name and script:
            gdm_entity_map[name.lower()] = script

    # 从 GDM code_modules 提取脚本映射
    for mod in gdm.get("code_modules", []):
        mod_name = mod.get("module_name", "")
        if mod_name:
            gdm_entity_map[mod_name.lower()] = mod_name

    # 构建实体列表
    entities: List[EntityIR] = []
    for ent_def in tpl["default_entities"]:
        name = ent_def["name"]
        script = ent_def.get("script")

        # 尝试用 GDM 的实体名/脚本覆盖
        name_lower = name.lower()
        if name_lower in gdm_entity_map:
            script = gdm_entity_map[name_lower]

        # 用 GDM scenes 的 required_objects 来调整数量
        count = ent_def.get("count", 1)
        for scene in gdm.get("scenes", []):
            required = scene.get("required_objects", [])
            match_count = sum(1 for r in required if name_lower in r.lower())
            if match_count > 0:
                count = max(count, match_count)

        entities.append(EntityIR(
            name=name,
            role=ent_def["role"],
            count=count,
            spawn_zone=ent_def.get("spawn_zone", "center"),
            script=script,
        ))

    # 场景名
    scene_name = "GameScene"
    for s in gdm.get("scenes", []):
        sn = s.get("scene_name", "")
        if sn:
            scene_name = sn
            break

    # 主题
    theme = gdm.get("game_title", None)

    # 相机
    cam_def = tpl.get("camera", {})
    camera_mode = cam_def.get("mode", "2d_side_view")
    # GDM camera_mode 覆盖
    gdm_cam = gdm.get("camera_mode", "")
    if "top" in gdm_cam.lower() or "俯" in gdm_cam:
        camera_mode = "top_down"
    elif "first" in gdm_cam.lower() or "第一人称" in gdm_cam:
        camera_mode = "3d_first_person"
    elif "third" in gdm_cam.lower() or "第三人称" in gdm_cam:
        camera_mode = "3d_third_person"
    elif "2d" in gdm_cam.lower() or "横" in gdm_cam or "side" in gdm_cam.lower():
        camera_mode = "2d_side_view"

    return SceneIR(
        scene_name=scene_name,
        genre=tpl["genre"],
        layout=tpl["layout"],
        difficulty="easy",
        camera=CameraIR(
            mode=camera_mode,
            follow_target=cam_def.get("follow_target", "Player"),
            background=cam_def.get("background", "sky_blue"),
        ),
        entities=entities,
        theme=theme,
    )
