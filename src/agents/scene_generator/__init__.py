"""GameForge - 场景生成Agent

分析游戏需求和任务计划，生成Unity场景描述JSON并发送到Unity Editor构建场景。
与代码生成并行执行，不阻塞主workflow。
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

import structlog

from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.engine.unity.unity_http_client import UnityHTTPClient

logger = structlog.get_logger()


class SceneGeneratorAgent(BaseAgent):
    """场景生成Agent - 生成Unity场景描述并发送到Unity Editor"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.SCENE_GENERATOR, config)
        unity_config = config.get("unity", {})
        self.auto_build_scene = unity_config.get("auto_build_scene", False)
        self.unity_client = UnityHTTPClient(
            host="localhost",
            port=unity_config.get("http_port", 8765),
            timeout=60.0,
        )

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行场景生成

        始终生成场景描述JSON，无论Unity Editor是否在线。
        仅在auto_build_scene=True且Unity在线时才发送到Unity构建。

        Args:
            state: 当前游戏开发状态

        Returns:
            场景生成结果，scene_status为 built/skipped/error
        """
        requirements = state.get("project_context", {}).get("requirements", "")
        engine = state.get("project_context", {}).get("engine", "unity")
        task_plan = state.get("task_plan", [])
        gdm = state.get("game_design_model")
        file_metadata = state.get("file_metadata", {})

        if engine != "unity":
            return {"scene_status": "skipped", "scene_skip_reason": "unsupported_engine", "message": "仅支持Unity引擎场景生成"}

        # Step 1: Always generate scene description first
        self.log_action("generating_scene_description", {
            "requirements_length": len(requirements),
            "task_count": len(task_plan),
            "has_gdm": gdm is not None,
            "metadata_files": len(file_metadata),
        })

        scene_desc = await self._generate_scene_description(requirements, task_plan, engine, gdm, file_metadata)

        if scene_desc is None:
            return {"scene_status": "error", "scene_error": "LLM未能生成有效的场景描述"}

        # Step 2: Check if auto_build is enabled
        if not self.auto_build_scene:
            self.log_action("scene_build_skipped", {"reason": "auto_build_scene_disabled"})
            return {
                "scene_status": "skipped",
                "scene_skip_reason": "auto_build_disabled",
                "scene_description": scene_desc,
                "message": "场景描述已生成，自动构建已关闭（unity.auto_build_scene=false）",
            }

        # Step 3: Check Unity Editor health
        is_alive = await self.unity_client.check_health()
        if not is_alive:
            self.log_action("scene_build_skipped", {"reason": "unity_http_unavailable"})
            return {
                "scene_status": "skipped",
                "scene_skip_reason": "unity_http_unavailable",
                "scene_description": scene_desc,
                "message": "Unity Editor HTTP服务器未运行，场景描述已生成但未构建",
            }

        # Step 4: Import generated code files to Unity Editor
        code_files = state.get("code_generated", {})
        cs_files = {k: v for k, v in code_files.items() if k.endswith(".cs")}
        if cs_files:
            self.log_action("importing_code_to_unity", {"file_count": len(cs_files)})
            import_result = await self.unity_client.import_files(cs_files)
            if import_result.get("status") != "success":
                self.log_error("import_failed", {"error": import_result.get("error", "未知")})

        # Step 5: Send scene description to Unity Editor
        self.log_action("sending_scene_to_unity", {
            "object_count": len(scene_desc.get("game_objects", [])),
        })

        result = await self.unity_client.send_scene(scene_desc)

        if result.get("status") != "success":
            return {
                "scene_status": "error",
                "scene_error": result.get("error", "未知错误"),
                "scene_description": scene_desc,
            }

        # Step 6: Trigger compilation
        self.log_action("compiling_unity_scripts")
        compile_result = await self.unity_client.compile_scripts()
        compile_errors = compile_result.get("errors", [])

        return {
            "scene_status": "built",
            "scene_description": scene_desc,
            "scene_path": result.get("scene_path", ""),
            "object_count": result.get("object_count", 0),
            "compile_status": compile_result.get("status", "unknown"),
            "compile_errors": compile_errors,
        }

    async def _generate_scene_description(
        self, requirements: str, task_plan: List[Dict], engine: str,
        gdm: Optional[Dict] = None, file_metadata: Optional[Dict] = None
    ) -> Optional[Dict]:
        """三级降级生成场景描述：
        Level 1: 模板匹配 → 确定性 IR → scene_description
        Level 2: LLM 生成 Scene IR → Pydantic 校验 → scene_description
        Level 3: 硬编码 fallback 场景
        """
        gdm = gdm or {}
        file_metadata = file_metadata or {}

        # ── Level 1: 模板匹配 ────────────────────────────────
        try:
            from src.agents.scene_templates import match_template, fill_template
            from src.agents.scene_ir_to_desc import ir_to_scene_description

            tpl_name = match_template(gdm)
            if tpl_name:
                ir = fill_template(tpl_name, gdm)
                scene_desc = ir_to_scene_description(ir, file_metadata)
                self.log_action("scene_generated_via_template", {"template": tpl_name})
                return scene_desc
        except Exception as e:
            self.log_error("template_scene_failed", {"error": str(e)})

        # ── Level 2: LLM 生成 Scene IR ──────────────────────
        try:
            scene_desc = await self._generate_via_llm_ir(requirements, task_plan, gdm, file_metadata)
            if scene_desc:
                return scene_desc
        except Exception as e:
            self.log_error("llm_ir_scene_failed", {"error": str(e)})

        # ── Level 3: 硬编码 fallback ─────────────────────────
        return self._fallback_scene(requirements, task_plan)

    async def _generate_via_llm_ir(
        self, requirements: str, task_plan: List[Dict],
        gdm: Dict, file_metadata: Dict,
    ) -> Optional[Dict]:
        """LLM 生成 Scene IR（抽象中间表示），然后确定性转换为 scene_description。"""
        from src.utils.llm_client import get_llm_client
        from src.agents.scene_ir import repair_scene_ir
        from src.agents.scene_ir_to_desc import ir_to_scene_description

        llm = get_llm_client(self.config)

        # 构建简洁的上下文
        task_summary = ""
        for t in task_plan:
            task_summary += f"- {t.get('name', '')}: {t.get('description', '')}\n"

        entity_lines = ""
        for ent in gdm.get("entities", []):
            entity_lines += f"- {ent.get('name', '')} ({ent.get('role', '')}): {ent.get('components', [])}\n"

        script_lines = ""
        for fpath, meta in file_metadata.items():
            if fpath.endswith(".cs"):
                script_lines += f"- {meta.get('class_name', '')} → {meta.get('target_game_object', '')}\n"

        user_prompt = f"""生成场景中间表示（Scene IR），只需输出JSON。

## 游戏需求
{requirements}

## 任务
{task_summary}

## GDM
- 类型: {gdm.get('genre', '')}
- 视角: {gdm.get('camera_mode', '')}
- 核心循环: {gdm.get('core_loop', '')}

## 实体
{entity_lines}

## 可用脚本
{script_lines}

## Scene IR JSON 格式
```json
{{
  "scene_name": "GameScene",
  "genre": "platformer|shooter|rpg|puzzle|runner|tower_defense",
  "layout": "linear|arena|open_world|grid|room_based",
  "difficulty": "easy|medium|hard",
  "camera": {{"mode": "2d_side_view|top_down|3d_third_person|3d_first_person", "follow_target": "Player", "background": "sky_blue|space_black|forest_green|dungeon_dark|sunset_orange|warm_beige"}},
  "entities": [
    {{"name": "Player", "role": "player|ground|platform|obstacle|enemy|npc|pickup|spawner|manager|decoration|boundary", "count": 1, "spawn_zone": "center|top|bottom|left|right|random", "script": "PlayerController"}}
  ],
  "theme": "森林|太空|地下城 等（可选）"
}}
```

只输出JSON，无额外文本。"""

        try:
            response = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是场景设计助手。根据游戏需求输出 Scene IR JSON（抽象中间表示），不要输出完整的 Unity 场景 JSON。只输出JSON。"},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
            )

            raw_ir = self._extract_json(response)
            if not raw_ir:
                return None

            # Pydantic 校验 + 自动修复
            ir = repair_scene_ir(raw_ir, gdm)

            # 确定性转换
            scene_desc = ir_to_scene_description(ir, file_metadata)
            self.log_action("scene_generated_via_llm_ir", {"genre": ir.genre, "entities": len(ir.entities)})
            return scene_desc

        except Exception as e:
            self.log_error("llm_ir_parse_failed", {"error": str(e)})
            return None

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从LLM响应中提取JSON（委托给统一提取器）"""
        from src.utils.json_extractor import extract_json
        return extract_json(text)

    def _fallback_scene(self, requirements: str, task_plan: List[Dict]) -> Dict:
        """LLM失败时的回退场景"""
        req_lower = requirements.lower()

        # Detect game type from requirements
        if any(w in req_lower for w in ["太空", "射击", "space", "shooter"]):
            return self._space_shooter_scene()
        elif any(w in req_lower for w in ["rpg", "回合", "战斗", "角色扮演"]):
            return self._rpg_scene()
        else:
            # Default: 2D platformer
            return self._platformer_scene()

    def _platformer_scene(self) -> Dict:
        """2D平台跳跃场景 — 彩色几何体版（无需美术资源）"""
        return {
            "scene_name": "GameScene",
            "new_scene": True,
            "camera": {
                "position": [0, 1, -10],
                "orthographic": True,
                "orthographic_size": 6,
                "background_color": [0.4, 0.7, 1.0, 1.0],
            },
            "lighting": {"type": "directional", "intensity": 1.0, "rotation": [50, -30, 0]},
            "game_objects": [
                {
                    "name": "Player", "type": "Capsule", "position": [0, 1, 0],
                    "tag": "Player", "layer": 0,
                    "color": [0.42, 0.55, 1.0, 1.0],
                    "scale": [0.8, 1.0, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"mass": "1", "gravityScale": "2", "freezeRotation": "true", "interpolation": "Interpolate", "collisionDetectionMode": "Continuous"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.9]"}},
                        {"type": "PlayerController", "properties": {"moveSpeed": "5", "jumpForce": "10"}},
                    ],
                },
                {
                    "name": "Ground", "type": "Cube", "position": [0, -2, 0],
                    "layer": 8, "is_static": True,
                    "color": [0.20, 0.83, 0.60, 1.0],
                    "scale": [14, 0.5, 1],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[14, 1]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Platform1", "type": "Cube", "position": [-3, 0, 0], "layer": 8, "is_static": True,
                    "color": [0.30, 0.69, 0.49, 1.0],
                    "scale": [3, 0.5, 1],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[3, 0.5]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Platform2", "type": "Cube", "position": [3, 1, 0], "layer": 8, "is_static": True,
                    "color": [0.30, 0.69, 0.49, 1.0],
                    "scale": [3, 0.5, 1],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[3, 0.5]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Coin1", "type": "Sphere", "position": [-3, 1, 0],
                    "color": [0.98, 0.75, 0.14, 1.0],
                    "scale": [0.5, 0.5, 0.5],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[0.5, 0.5]", "isTrigger": "true"}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Coin2", "type": "Sphere", "position": [3, 2, 0],
                    "color": [0.98, 0.75, 0.14, 1.0],
                    "scale": [0.5, 0.5, 0.5],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[0.5, 0.5]", "isTrigger": "true"}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "Cube", "position": [2, -1, 0],
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "3", "freezeRotation": "true"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                        {"type": "EnemyController", "properties": {"moveSpeed": "2", "patrolDistance": "3"}},
                    ],
                },
                {
                    "name": "GameManager", "type": "Empty", "position": [0, 0, 0],
                    "components": [{"type": "GameManager", "properties": {}}],
                },
            ],
        }

    def _space_shooter_scene(self) -> Dict:
        """太空射击场景 — 彩色几何体版（无需美术资源）"""
        return {
            "scene_name": "SpaceShooterScene",
            "new_scene": True,
            "camera": {
                "position": [0, 0, -10],
                "orthographic": True,
                "orthographic_size": 8,
                "background_color": [0.0, 0.0, 0.1, 1.0],
            },
            "lighting": {"type": "directional", "intensity": 0.8, "rotation": [0, 0, 0]},
            "game_objects": [
                {
                    "name": "Player", "type": "Capsule", "position": [0, -4, 0],
                    "tag": "Player",
                    "color": [0.42, 0.55, 1.0, 1.0],
                    "scale": [0.8, 1.0, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[1, 1]"}},
                        {"type": "PlayerController", "properties": {}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "Cube", "position": [-3, 4, 0],
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                    ],
                },
                {
                    "name": "Enemy2", "type": "Cube", "position": [3, 5, 0],
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                    ],
                },
                {
                    "name": "GameBoundary", "type": "Empty", "position": [0, 0, 0],
                    "components": [{"type": "GameManager", "properties": {}}],
                },
                {
                    "name": "EnemySpawner", "type": "Empty", "position": [0, 6, 0],
                    "components": [{"type": "EnemySpawner", "properties": {}}],
                },
            ],
        }

    def _rpg_scene(self) -> Dict:
        """RPG场景"""
        return {
            "scene_name": "RPGScene",
            "new_scene": True,
            "camera": {
                "position": [0, 5, -10],
                "orthographic": True,
                "orthographic_size": 8,
                "background_color": [0.2, 0.3, 0.2, 1.0],
            },
            "lighting": {"type": "directional", "intensity": 1.0, "rotation": [50, -30, 0]},
            "game_objects": [
                {
                    "name": "Ground", "type": "Plane", "position": [0, 0, 0],
                    "scale": [5, 1, 5],
                    "material": "ground",
                    "components": [],
                },
                {
                    "name": "Player", "type": "Capsule", "position": [0, 1, 0],
                    "tag": "Player",
                    "material": "player",
                    "components": [
                        {"type": "CharacterController", "properties": {}},
                        {"type": "PlayerController", "properties": {}},
                    ],
                },
                {
                    "name": "MagicCircle", "type": "Cylinder", "position": [3, 0.1, 0],
                    "scale": [1.5, 0.1, 1.5],
                    "material": "purple",
                    "particle_effect": "magic",
                },
                {
                    "name": "Campfire", "type": "Cylinder", "position": [-3, 0.5, 0],
                    "scale": [0.5, 0.5, 0.5],
                    "material": "brown",
                    "particle_effect": "fire",
                },
                {
                    "name": "Chest", "type": "Cube", "position": [2, 0.5, 2],
                    "scale": [0.8, 0.6, 0.6],
                    "material": "wood",
                    "particle_effect": "sparkle",
                },
                {
                    "name": "Tree1", "type": "Cylinder", "position": [-5, 1.5, -2],
                    "scale": [0.3, 3, 0.3],
                    "material": "wood",
                },
                {
                    "name": "TreeTop1", "type": "Sphere", "position": [-5, 3.5, -2],
                    "scale": [2, 2, 2],
                    "material": "green",
                },
                {
                    "name": "BattleManager", "type": "Empty", "position": [0, 0, 0],
                    "components": [{"type": "GameManager", "properties": {}}],
                },
                {
                    "name": "DirectionalLight", "type": "DirectionalLight", "position": [0, 10, 0],
                    "rotation": [50, -30, 0],
                    "components": [],
                },
            ],
        }

    def _default_system_prompt(self) -> str:
        return """你是Unity场景设计师。根据游戏需求生成场景描述JSON。

JSON格式：
{
  "scene_name": "场景名称",
  "new_scene": true,
  "camera": {"position": [x,y,z], "orthographic": true, "orthographic_size": 6, "background_color": [r,g,b,a]},
  "lighting": {"type": "directional", "intensity": 1.0, "rotation": [x,y,z]},
  "game_objects": [
    {
      "name": "对象名",
      "type": "Cube|Sphere|Cylinder|Plane|Quad|Sprite|Empty|Camera|DirectionalLight|PointLight",
      "position": [x,y,z],
      "rotation": [x,y,z],
      "scale": [x,y,z],
      "tag": "Player",
      "layer": 0,
      "is_static": false,
      "sprite": "精灵名（可选，仅Sprite类型）",
      "color": [r,g,b,a],
      "material": "预设名（可选）",
      "particle_effect": "特效名（可选）",
      "components": [
        {"type": "组件名", "properties": {"属性名": "值"}}
      ]
    }
  ]
}

支持的组件类型：Rigidbody, Rigidbody2D, BoxCollider, SphereCollider, BoxCollider2D, CircleCollider2D, SpriteRenderer, Animator
自定义脚本：直接使用类名如 PlayerController, GameManager, EnemyController, CoinController

可用素材（sprite字段可填，也可使用别名如player/ground/coin/enemy自动匹配）：
- 角色：character_purple_idle, character_purple_front, character_purple_walk_a, character_purple_jump, character_purple_climb_a, character_purple_duck, character_purple_hit, character_beige_idle, character_beige_front, character_green_idle, character_green_front, character_pink_idle, character_pink_front
- 地形：terrain_grass_block, terrain_grass_block_top, terrain_dirt_block, terrain_sand_block, terrain_stone_block, terrain_snow_block
- 方块：block_blue, block_green, block_red, block_yellow, block_coin, block_coin_active, block_plank, block_planks, block_spikes, block_exclamation, bomb, brick_brown, brick_grey
- 敌人：barnacle_attack_a, barnacle_attack_b, bee_a, bee_b, fly_a, fly_b, frog_idle, frog_jump, ladybug_fly, ladybug_walk_a, fish_blue_rest, fish_blue_swim_a, fish_purple_rest, block_idle, mouse_rest, snail_walk_a
- 背景：background_color_trees, background_color_hills, background_color_desert, background_color_mushrooms, background_clouds, background_solid_sky

也可以不填sprite字段，系统会根据对象名自动匹配素材。

颜色系统（color字段，[r,g,b,a] 0-1范围，如[1,0.5,0,1]是橙色）：
- 也可用material字段填预设名：red, green, blue, yellow, orange, purple, pink, cyan, white, black, gray, brown, gold, silver, player, enemy, ground, platform, coin, water, lava, ice, wood, stone, metal
- 不填color/material时，系统会根据对象名自动上色（Player=蓝, Enemy=红, Ground=绿, Coin=金）

粒子特效（particle_effect字段，添加到对象上）：
- fire — 火焰效果（适合火把、火焰陷阱）
- smoke — 烟雾效果（适合烟囱、爆炸后）
- explosion — 爆炸效果（适合敌人死亡、炸弹爆炸）
- sparkle — 闪烁光点（适合金币、宝石、魔法物品）
- heal — 治愈效果（适合治疗道具、回血点）
- dust — 灰尘效果（适合落地、移动）
- trail — 拖尾效果（适合子弹、快速移动物体）
- magic — 魔法效果（适合魔法攻击、传送门）
- rain — 雨滴效果（适合雨天场景）
- snow — 雪花效果（适合雪天场景）

规则：
- 只输出JSON，无额外文本
- 坐标使用Unity坐标系（Y轴向上）
- 2D游戏使用Rigidbody2D和BoxCollider2D
- 3D游戏使用Rigidbody和BoxCollider
- 善用color和particle_effect让场景更生动
- Sprite类型对象建议填写sprite字段以获得更好的视觉效果

层设置（layer字段，非常重要！）：
- 0 = Default（默认）
- 8 = Ground（地面专用层）
- 所有地面、平台、墙壁等可站立的静态物体必须设layer: 8，否则角色无法检测到地面
- 地面对象必须添加Rigidbody2D（bodyType=Static）和BoxCollider2D
- Player必须添加Rigidbody2D、BoxCollider2D和PlayerController组件

性能优化（非常重要！）：
- 总GameObject数量控制在15个以内
- 地面/平台合并：用1个大BoxCollider2D覆盖整块地面（如size=[14,1]），不要为每个格子创建单独对象
- 静态物体设is_static: true，减少物理计算
- 粒子特效最多用2-3个，不要每个对象都加
- 敌人/动态对象控制在3个以内
- 不需要Rigidbody2D的对象（如金币、触发器）不要添加Rigidbody2D
- Player的Rigidbody2D设置：interpolation=Interpolate（平滑移动）, collisionDetectionMode=Continuous（防穿透）
- 静态Rigidbody2D不需要设interpolation

游戏完整性（非常重要！）：
- 2D平台游戏必须包含：Player（带PlayerController）、Ground（layer=8）、Platform（layer=8）、Enemy（带EnemyController）、Coin（带CoinController+触发器）、GameManager
- PlayerController组件自动加载sprite动画（idle/walk/jump），无需手动配置sprite引用
- EnemyController需要Rigidbody2D（gravityScale>0）和BoxCollider2D，会自动加载敌人sprite动画
- CoinController需要BoxCollider2D（isTrigger=true），金币会自动上下浮动
- GameManager管理游戏状态（分数、生命、游戏结束），必须有且仅有一个
- 场景必须是一个可玩的完整游戏，不能只是几个散落的对象
"""
