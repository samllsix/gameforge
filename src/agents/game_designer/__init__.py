"""GameForge - 游戏设计Agent

将用户需求转化为结构化的 Game Design Model (GDM)，
作为 Planner、CodeGenerator、SceneGenerator 的共同输入。
"""

import json
import re
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.utils.llm_client import get_llm_client

# 预编译的正则表达式（模块级常量）
_TITLE_PATTERNS = [
    re.compile(r"(?:游戏名|项目名|叫做|叫|名称)[：:]?\s*[「「]?(.+?)[」」]?\s*$", re.MULTILINE),
    re.compile(r"^(.+?)(?:游戏|项目|是一款)", re.MULTILINE),
]


class GameDesignerAgent(BaseAgent):
    """游戏设计Agent — 生成 Game Design Model"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.GAME_DESIGNER, config)
        self.llm = get_llm_client(config)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("game_designer_execute")
        requirements = state.get("project_context", {}).get("requirements", "")
        engine = state.get("project_context", {}).get("engine", "unity")

        gdm = await self.generate_design_model(requirements, engine)
        if not gdm:
            self.log_error("gdm_generation_failed")
            gdm = self._fallback_gdm(requirements, engine)

        return {
            "game_design_model": gdm,
            "current_phase": "design_complete",
        }

    async def generate_design_model(self, requirements: str, engine: str) -> Optional[Dict]:
        """调用LLM生成 Game Design Model"""
        system_prompt = self._system_prompt()
        user_prompt = f"""请根据以下游戏需求，生成完整的 Game Design Model JSON。

游戏引擎: {engine}
用户需求:
{requirements}

请严格按照系统提示中的JSON Schema输出，不要输出额外文本。"""

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=8192,
            )

            gdm = self._extract_json(response)
            if gdm and "game_title" in gdm:
                return self._normalize_gdm(gdm, requirements, engine)

            self.log_error("no_valid_gdm_in_response", {"preview": response[:300]})
            return self._fallback_gdm(requirements, engine)

        except Exception as e:
            self.log_error("gdm_llm_error", {"error": str(e)})
            return self._fallback_gdm(requirements, engine)

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从LLM响应中提取JSON（委托给统一提取器）"""
        from src.utils.json_extractor import extract_json
        return extract_json(text)

    def _normalize_gdm(self, gdm: Dict, requirements: str, engine: str) -> Dict:
        """规范化GDM，补全缺失字段"""
        defaults = {
            "game_title": "未命名游戏",
            "genre": "unknown",
            "engine": engine,
            "camera_mode": "2D side-scroller",
            "core_loop": "",
            "player_actions": [],
            "win_conditions": [],
            "fail_conditions": [],
            "main_systems": [],
            "entities": [],
            "scenes": [],
            "code_modules": [],
            "assets_needed": {},
            "input_map": [],
            "tags_layers": {"tags": [], "layers": []},
            "physics_settings": {},
        }
        for key, default in defaults.items():
            if key not in gdm:
                gdm[key] = default

        # 确保 main_systems 是列表
        if isinstance(gdm["main_systems"], list):
            normalized_systems = []
            for sys in gdm["main_systems"]:
                if isinstance(sys, str):
                    normalized_systems.append({"name": sys, "description": "", "priority": "medium"})
                elif isinstance(sys, dict):
                    normalized_systems.append(sys)
            gdm["main_systems"] = normalized_systems

        # 确保 entities 是列表
        if isinstance(gdm["entities"], list):
            normalized_entities = []
            for ent in gdm["entities"]:
                if isinstance(ent, str):
                    normalized_entities.append({"name": ent, "role": "environment", "components": []})
                elif isinstance(ent, dict):
                    normalized_entities.append(ent)
            gdm["entities"] = normalized_entities

        # 确保 code_modules 是列表
        if isinstance(gdm["code_modules"], list):
            normalized_modules = []
            for mod in gdm["code_modules"]:
                if isinstance(mod, str):
                    normalized_modules.append({"module_name": mod, "responsibility": "", "output_files": []})
                elif isinstance(mod, dict):
                    normalized_modules.append(mod)
            gdm["code_modules"] = normalized_modules

        return gdm

    def _fallback_gdm(self, requirements: str, engine: str) -> Dict:
        """LLM失败时的回退GDM — 基于关键词分析"""
        req_lower = requirements.lower()

        # 检测游戏类型
        genre = "platformer"
        camera_mode = "2D side-scroller"
        if any(w in req_lower for w in ["射击", "shooter", "太空", "space"]):
            genre = "shooter"
            camera_mode = "2D top-down"
        elif any(w in req_lower for w in ["rpg", "回合", "角色扮演", "turn-based"]):
            genre = "rpg"
            camera_mode = "2D top-down"
        elif any(w in req_lower for w in ["3d", "第三人称", "third-person"]):
            genre = "adventure"
            camera_mode = "3D third-person"
        elif any(w in req_lower for w in ["塔防", "tower defense"]):
            genre = "tower_defense"
            camera_mode = "2D top-down"
        elif any(w in req_lower for w in ["跑酷", "endless", "runner"]):
            genre = "runner"
            camera_mode = "2D side-scroller"
        elif any(w in req_lower for w in ["解谜", "puzzle"]):
            genre = "puzzle"
            camera_mode = "2D top-down"

        # 基础系统
        systems = [
            {"name": "movement", "description": "玩家移动控制", "priority": "high"},
            {"name": "scoring", "description": "计分系统", "priority": "medium"},
            {"name": "UI", "description": "游戏UI界面", "priority": "medium"},
        ]

        # 根据需求添加系统
        if any(w in req_lower for w in ["战斗", "combat", "攻击", "attack", "伤害"]):
            systems.append({"name": "combat", "description": "战斗/伤害系统", "priority": "high"})
        if any(w in req_lower for w in ["敌人", "enemy", "ai", "怪物"]):
            systems.append({"name": "enemy_ai", "description": "敌人AI行为", "priority": "high"})
        if any(w in req_lower for w in ["道具", "item", "inventory", "背包", "拾取"]):
            systems.append({"name": "inventory", "description": "道具/背包系统", "priority": "medium"})
        if any(w in req_lower for w in ["对话", "dialogue", "npc", "任务"]):
            systems.append({"name": "dialogue", "description": "对话/任务系统", "priority": "low"})
        if any(w in req_lower for w in ["存档", "save", "加载", "load"]):
            systems.append({"name": "save/load", "description": "存档加载系统", "priority": "low"})
        if any(w in req_lower for w in ["音效", "audio", "音乐", "music", "sound"]):
            systems.append({"name": "audio", "description": "音效管理", "priority": "low"})
        if any(w in req_lower for w in ["关卡", "level", "地图", "map"]):
            systems.append({"name": "level", "description": "关卡管理", "priority": "medium"})

        # 基础实体
        entities = [
            {"name": "Player", "role": "player", "components": ["Rigidbody2D", "BoxCollider2D", "SpriteRenderer"]},
            {"name": "GameManager", "role": "manager", "components": []},
        ]

        if any(w in req_lower for w in ["敌人", "enemy", "怪物", "monster"]):
            entities.append({"name": "Enemy", "role": "enemy", "components": ["Rigidbody2D", "BoxCollider2D", "SpriteRenderer"]})
        if any(w in req_lower for w in ["金币", "coin", "道具", "item", "拾取", "pickup"]):
            entities.append({"name": "Pickup", "role": "pickup", "components": ["BoxCollider2D", "SpriteRenderer"]})
        if any(w in req_lower for w in ["npc", "村民", "商人"]):
            entities.append({"name": "NPC", "role": "npc", "components": ["BoxCollider2D", "SpriteRenderer"]})

        # 代码模块
        code_modules = [
            {
                "module_name": "PlayerController",
                "responsibility": "玩家移动、跳跃控制",
                "output_files": ["Assets/Scripts/Player/PlayerController.cs"],
                "dependencies": [],
                "target_game_objects": ["Player"],
                "required_components": ["Rigidbody2D", "BoxCollider2D"],
            },
            {
                "module_name": "GameManager",
                "responsibility": "游戏状态管理、计分",
                "output_files": ["Assets/Scripts/Core/GameManager.cs"],
                "dependencies": [],
                "target_game_objects": ["GameManager"],
                "required_components": [],
            },
        ]

        if any(w in req_lower for w in ["敌人", "enemy"]):
            code_modules.append({
                "module_name": "EnemyController",
                "responsibility": "敌人AI行为",
                "output_files": ["Assets/Scripts/Enemy/EnemyController.cs"],
                "dependencies": [],
                "target_game_objects": ["Enemy"],
                "required_components": ["Rigidbody2D", "BoxCollider2D"],
            })

        if any(w in req_lower for w in ["金币", "coin", "道具", "item", "拾取", "collectible"]):
            code_modules.append({
                "module_name": "CoinController",
                "responsibility": "金币/道具收集逻辑",
                "output_files": ["Assets/Scripts/Collectibles/CoinController.cs"],
                "dependencies": ["GameManager"],
                "target_game_objects": ["Coin"],
                "required_components": ["BoxCollider2D"],
            })

        return {
            "game_title": self._extract_title(requirements),
            "genre": genre,
            "engine": engine,
            "camera_mode": camera_mode,
            "core_loop": f"玩家控制角色，完成游戏目标",
            "player_actions": ["移动", "跳跃"] if "2d" in camera_mode or "side" in camera_mode else ["移动"],
            "win_conditions": ["完成关卡目标"],
            "fail_conditions": ["生命值归零"],
            "main_systems": systems,
            "entities": entities,
            "scenes": [
                {
                    "scene_name": "GameScene",
                    "purpose": "主游戏场景",
                    "required_objects": [e["name"] for e in entities],
                    "spawn_points": [{"name": "PlayerSpawn", "position": [0, 1, 0]}],
                    "camera_setup": {"mode": camera_mode, "follow_target": "Player"},
                    "lighting_setup": {"type": "directional", "intensity": 1.0},
                    "UI_canvas": {"mode": "ScreenSpace-Overlay", "elements": ["ScoreText", "HealthBar"]},
                }
            ],
            "code_modules": code_modules,
            "assets_needed": {
                "sprites": ["player", "ground", "enemy", "coin"],
                "materials": [],
                "audio": [],
                "animations": [],
                "UI_assets": ["health_bar", "score_font"],
            },
            "input_map": [
                {"name": "Horizontal", "type": "axis", "description": "水平移动"},
                {"name": "Jump", "type": "button", "key": "Space", "description": "跳跃"},
            ],
            "tags_layers": {
                "tags": ["Player", "Enemy", "Pickup"],
                "layers": [
                    {"name": "Ground", "index": 8},
                    {"name": "Player", "index": 9},
                ],
            },
            "physics_settings": {
                "gravity": [0, -9.81, 0] if "3d" in camera_mode else [0, -20, 0],
                "default_material": {"friction": 0.4, "bounciness": 0},
            },
        }

    def _extract_title(self, requirements: str) -> str:
        """从需求中提取游戏标题"""
        for pattern in _TITLE_PATTERNS:
            match = pattern.search(requirements)
            if match:
                title = match.group(1).strip()
                if 2 <= len(title) <= 20:
                    return title
        return "GameForge游戏"

    def _system_prompt(self) -> str:
        """从配置文件加载系统提示"""
        return self.get_prompt_template("game_designer_system") or self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        """默认系统提示（文件加载失败时使用）"""
        return """你是游戏设计专家。根据用户需求生成结构化的 Game Design Model (GDM) JSON。

输出JSON Schema：
{
  "game_title": "游戏名称",
  "genre": "游戏类型(platformer/shooter/rpg/puzzle/adventure/tower_defense/runner/simulation/fighting)",
  "camera_mode": "视角(2D side-scroller/2D top-down/3D third-person/3D first-person/isometric/UI/menu-driven)",
  "core_loop": "核心游戏循环描述",
  "player_actions": ["玩家可执行的动作列表"],
  "win_conditions": ["胜利条件"],
  "fail_conditions": ["失败条件"],
  "main_systems": [
    {
      "name": "系统名",
      "description": "系统描述",
      "priority": "high/medium/low"
    }
  ],
  "entities": [
    {
      "name": "实体名",
      "role": "角色类型",
      "components": ["Unity组件列表"]
    }
  ],
  "code_modules": [
    {
      "module_name": "模块名(类名)",
      "responsibility": "模块职责描述",
      "output_files": ["输出文件路径"],
      "dependencies": ["依赖模块"],
      "target_game_objects": ["GameObject名"],
      "required_components": ["组件列表"]
    }
  ]
}

规则：
- 只输出JSON，无额外文本
- code_modules 中的类名必须是合法C#标识符
- 2D游戏使用 Rigidbody2D/BoxCollider2D，3D游戏使用 Rigidbody/BoxCollider
- 不要遗漏用户需求中提到的任何功能"""
