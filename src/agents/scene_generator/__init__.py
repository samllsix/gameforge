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
        """调用LLM生成场景描述JSON — 基于Game Design Model和代码元数据"""
        from src.utils.llm_client import get_llm_client

        llm = get_llm_client(self.config)

        system_prompt = self.get_prompt_template("scene_generator_system")
        if not system_prompt:
            system_prompt = self._default_system_prompt()

        # Build task summary
        task_summary = ""
        for t in task_plan:
            task_summary += f"- {t.get('name', '未知')}: {t.get('description', '')}\n"

        # GDM context
        gdm_context = ""
        if gdm:
            gdm_context = f"""
## 游戏设计模型
- 游戏名称: {gdm.get('game_title', '')}
- 游戏类型: {gdm.get('genre', '')}
- 视角模式: {gdm.get('camera_mode', '')}
- 核心循环: {gdm.get('core_loop', '')}

### 实体定义
"""
            for ent in gdm.get("entities", []):
                gdm_context += f"- {ent.get('name', '')} (角色: {ent.get('role', '')}): 组件 {ent.get('components', [])}\n"

            # 场景定义
            scenes = gdm.get("scenes", [])
            if scenes:
                gdm_context += "\n### 场景定义\n"
                for scene in scenes:
                    gdm_context += f"- {scene.get('scene_name', '')}: {scene.get('purpose', '')}\n"
                    gdm_context += f"  需要的对象: {scene.get('required_objects', [])}\n"
                    if scene.get("spawn_points"):
                        gdm_context += f"  生成点: {scene.get('spawn_points', [])}\n"
                    if scene.get("UI_canvas"):
                        gdm_context += f"  UI: {scene.get('UI_canvas', {})}\n"

            # 输入映射
            input_map = gdm.get("input_map", [])
            if input_map:
                gdm_context += "\n### 输入映射\n"
                for inp in input_map:
                    gdm_context += f"- {inp.get('name', '')} ({inp.get('type', '')}): {inp.get('description', '')}\n"

            # Tags/Layers
            tags_layers = gdm.get("tags_layers", {})
            if tags_layers.get("tags"):
                gdm_context += f"\n### 必需Tags: {', '.join(tags_layers['tags'])}\n"
            if tags_layers.get("layers"):
                gdm_context += f"### 必需Layers: {', '.join(l.get('name', '') + '(' + str(l.get('index', '')) + ')' for l in tags_layers['layers'])}\n"

        # Code metadata context — 确保场景挂载真实存在的脚本
        metadata_context = ""
        if file_metadata:
            metadata_context = "\n## 已生成的代码文件（场景中只能挂载这些脚本）\n"
            for fpath, meta in file_metadata.items():
                if fpath.endswith(".cs"):
                    cls = meta.get("class_name", "")
                    target = meta.get("target_game_object", "")
                    comps = meta.get("required_components", [])
                    metadata_context += f"- {fpath}: 类名={cls}, 挂载对象={target}, 需要组件={comps}\n"

        user_prompt = f"""请根据以下游戏需求、任务计划和已生成代码，生成Unity场景描述JSON。

## 游戏需求
{requirements}

## 任务计划
{task_summary}
{gdm_context}
{metadata_context}

## 输出要求
- 只输出JSON，无额外文本
- JSON格式必须符合SceneDescription规范
- game_objects中的组件type使用Unity组件全名（如Rigidbody2D, BoxCollider2D）或脚本类名
- 所有坐标使用Unity坐标系（Y轴向上）
- 场景中的脚本组件必须来自已生成的代码文件
- 根据游戏设计模型中的实体定义创建对应的GameObject
- 根据场景定义放置对象、设置相机和灯光
- 如果有UI_canvas定义，创建UI Canvas和相关UI元素
- 如果有spawn_points，在场景中放置生成点
- 确保Player有PlayerController组件，Enemy有EnemyController组件等
"""

        try:
            response = await llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )

            # Extract JSON from response
            scene_json = self._extract_json(response)
            if scene_json:
                return scene_json

            self.log_error("no_valid_json_in_response", {"response_preview": response[:200]})
            return self._fallback_scene(requirements, task_plan)

        except Exception as e:
            self.log_error("llm_call_failed", {"error": str(e)})
            return self._fallback_scene(requirements, task_plan)

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从LLM响应中提取JSON"""
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from code block
        patterns = [
            r"```json\s*\n([\s\S]*?)\n```",
            r"```\s*\n([\s\S]*?)\n```",
            r"(\{[\s\S]*\"game_objects\"[\s\S]*\})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue

        return None

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
        """2D平台跳跃场景 — 完整版"""
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
                    "name": "Player", "type": "Sprite", "position": [0, 1, 0],
                    "tag": "Player", "layer": 0,
                    "sprite": "character_purple_idle",
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"mass": "1", "gravityScale": "2", "freezeRotation": "true", "interpolation": "Interpolate", "collisionDetectionMode": "Continuous"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.9]"}},
                        {"type": "PlayerController", "properties": {"moveSpeed": "5", "jumpForce": "10"}},
                    ],
                },
                {
                    "name": "Ground", "type": "Sprite", "position": [0, -2, 0],
                    "layer": 8, "is_static": True,
                    "sprite": "terrain_grass_block_top",
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[14, 1]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Platform1", "type": "Sprite", "position": [-3, 0, 0], "layer": 8, "is_static": True,
                    "sprite": "terrain_grass_block",
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[3, 0.5]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Platform2", "type": "Sprite", "position": [3, 1, 0], "layer": 8, "is_static": True,
                    "sprite": "terrain_grass_block",
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[3, 0.5]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Coin1", "type": "Sprite", "position": [-3, 1, 0],
                    "sprite": "block_coin",
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[0.5, 0.5]", "isTrigger": "true"}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Coin2", "type": "Sprite", "position": [3, 2, 0],
                    "sprite": "block_coin",
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[0.5, 0.5]", "isTrigger": "true"}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "Sprite", "position": [2, -1, 0],
                    "sprite": "bee_a",
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
        """太空射击场景"""
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
                    "name": "Player", "type": "Sprite", "position": [0, -4, 0],
                    "tag": "Player",
                    "sprite": "character_purple_front",
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[1, 1]"}},
                        {"type": "PlayerController", "properties": {}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "Sprite", "position": [-3, 4, 0],
                    "sprite": "bee_a",
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                    ],
                },
                {
                    "name": "Enemy2", "type": "Sprite", "position": [3, 5, 0],
                    "sprite": "fly_a",
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
