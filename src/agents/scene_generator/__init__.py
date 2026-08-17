"""GameForge - 场景生成Agent

分析游戏需求和任务计划，生成 Godot 场景描述并发送到 Godot Editor 构建场景。
与代码生成并行执行，不阻塞主workflow。
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

import structlog

from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.engine.godot.godot_http_client import GodotHTTPClient

logger = structlog.get_logger()


class SceneGeneratorAgent(BaseAgent):
    """场景生成Agent - 生成 Godot 场景描述并发送到 Godot Editor"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.SCENE_GENERATOR, config)
        godot_config = config.get("godot", {})
        self.auto_build_scene = godot_config.get("auto_build_scene", False)
        self.godot_client = GodotHTTPClient(
            base_url=f"http://{godot_config.get('host', 'localhost')}:{godot_config.get('http_port', 8765)}",
            timeout=60.0,
        )

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行场景生成

        始终生成场景描述JSON，无论 Godot Editor 是否在线。
        仅在 auto_build_scene=True 且 Godot 在线时才发送构建；
        Python 侧会先构建合法 .tscn 文本并交由插件落盘。

        Args:
            state: 当前游戏开发状态

        Returns:
            场景生成结果，scene_status为 built/skipped/error
        """
        requirements = state.get("project_context", {}).get("requirements", "")
        engine = state.get("project_context", {}).get("engine", "godot")
        task_plan = state.get("task_plan", [])
        gdm = state.get("game_design_model")
        file_metadata = state.get("file_metadata", {})

        if engine != "godot":
            return {"scene_status": "skipped", "scene_skip_reason": "unsupported_engine", "message": "仅支持 Godot 引擎场景生成"}

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
                "message": "场景描述已生成，自动构建已关闭（godot.auto_build_scene=false）",
            }

        # Step 3: Check Godot Editor health
        is_alive = await self.godot_client.check_health()
        if not is_alive:
            self.log_action("godot_http_unavailable_fallback_to_disk", {})
            tscn_text = None
            try:
                from src.engine.godot.scene_builder import GodotSceneBuilder
                tscn_text = GodotSceneBuilder(godot_version=4).build_tscn(scene_desc)
            except Exception as e:
                self.log_error("scene_tscn_build_failed_fallback", {"error": str(e)})

            if tscn_text:
                import os
                godot_config = self.config.get("godot", {})
                project_path = godot_config.get("project_path", "")
                if not project_path or project_path.startswith("${"):
                    project_path = os.getenv("GODOT_PROJECT_PATH", os.getcwd())
                scenes_dir = os.path.join(project_path, "scenes")
                os.makedirs(scenes_dir, exist_ok=True)
                scene_name = scene_desc.get("scene_name", "GameScene")
                tscn_path = os.path.join(scenes_dir, f"{scene_name}.tscn")
                try:
                    with open(tscn_path, "w", encoding="utf-8") as f:
                        f.write(tscn_text)

                    main_tscn_path = os.path.join(scenes_dir, "Main.tscn")
                    with open(main_tscn_path, "w", encoding="utf-8") as f:
                        f.write(tscn_text)

                    self._ensure_script_stubs(scene_desc, project_path)

                    self.log_action("scene_tscn_written_to_disk", {
                        "path": tscn_path, "size": len(tscn_text),
                        "object_count": len(scene_desc.get("game_objects", [])),
                    })
                    return {
                        "scene_status": "built",
                        "scene_description": scene_desc,
                        "scene_path": tscn_path,
                        "object_count": len(scene_desc.get("game_objects", [])),
                        "compile_status": "skipped",
                        "compile_errors": [],
                        "message": "Godot HTTP 不可用，.tscn 已直接写入磁盘",
                    }
                except Exception as e:
                    self.log_error("scene_tscn_write_failed", {"error": str(e)})
                    return {
                        "scene_status": "error",
                        "scene_error": f"写入 .tscn 文件失败: {e}",
                        "scene_description": scene_desc,
                    }

            return {
                "scene_status": "skipped",
                "scene_skip_reason": "godot_http_unavailable",
                "scene_description": scene_desc,
                "message": "Godot Editor HTTP 服务未运行，场景描述已生成但未构建",
            }

        # Step 4: Import generated code files to Godot Editor
        code_files = state.get("code_generated", {})
        gd_files = {k: v for k, v in code_files.items() if k.endswith(".gd")}
        if gd_files:
            self.log_action("importing_code_to_godot", {"file_count": len(gd_files)})
            import_result = await self.godot_client.import_files(gd_files)
            if import_result.get("status") != "success":
                self.log_error("import_failed", {"error": import_result.get("error", "未知")})

        # Step 5: 由 Python 侧构建合法 .tscn 文本（绕开插件端类型错配），再推给 Godot
        tscn_text = None
        try:
            from src.engine.godot.scene_builder import GodotSceneBuilder
            tscn_text = GodotSceneBuilder(godot_version=4).build_tscn(scene_desc)
            self.log_action("scene_tscn_built", {"tscn_len": len(tscn_text)})
        except Exception as e:
            self.log_error("scene_tscn_build_failed", {"error": str(e)})

        self.log_action("sending_scene_to_godot", {
            "object_count": len(scene_desc.get("game_objects", [])),
            "has_tscn": tscn_text is not None,
        })

        result = await self.godot_client.send_scene(scene_desc, tscn_text=tscn_text)

        if result.get("status") != "success":
            return {
                "scene_status": "error",
                "scene_error": result.get("error", "未知错误"),
                "scene_description": scene_desc,
            }

        # Step 6: Trigger compilation
        self.log_action("compiling_godot_scripts")
        compile_result = await self.godot_client.compile_scripts()
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

        llm = get_llm_client(self.config, provider=self.provider, model=self.model)

        # 构建简洁的上下文
        task_summary = ""
        for t in task_plan:
            task_summary += f"- {t.get('name', '')}: {t.get('description', '')}\n"

        entity_lines = ""
        for ent in gdm.get("entities", []):
            entity_lines += f"- {ent.get('name', '')} ({ent.get('role', '')}): {ent.get('components', [])}\n"

        script_lines = ""
        for fpath, meta in file_metadata.items():
            if fpath.endswith(".gd"):
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

## script 字段说明
- script 字段应为控制器类名（如 "PlayerController", "EnemyController", "ScoreManager"），不是实体名
- 静态物体（ground, platform, decoration, boundary）不需要 script，请省略该字段
- 只有需要自定义逻辑的实体才填写 script（如 player, enemy, pickup, manager）

只输出JSON，无额外文本。"""

        try:
            response = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是场景设计助手。根据游戏需求输出 Scene IR JSON（抽象中间表示），不要输出完整的 Godot 场景 JSON。只输出JSON。"},
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

    def _ensure_script_stubs(self, scene_desc: Dict[str, Any], project_path: str):
        """为场景中引用但磁盘上不存在的脚本创建功能性存根。"""
        import os
        import re

        scripts_dir = os.path.join(project_path, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)

        _IGNORED = {"rigidbody2d", "rigidbody", "rigidbody3d",
                     "charactercontroller", "transform", "audio_source",
                     "mesh_renderer", "mesh_filter", "sprite_renderer",
                     "characterbody2d", "characterbody3d", "staticbody2d",
                     "staticbody3d", "area2d", "area3d",
                     "camera2d", "camera3d", "node2d", "node3d", "node",
                     "collisionshape2d", "collisionshape3d", "sprite2d",
                     "animatedsprite2d", "tilemap", "canvaslayer",
                     "directionallight2d", "directionallight3d",
                     "pointlight2d", "pointlight3d", "label"}

        obj_role_map: Dict[str, str] = {}
        for obj in scene_desc.get("game_objects", []):
            obj_role_map[obj.get("name", "")] = obj.get("role", "")

        seen = set()
        for obj in scene_desc.get("game_objects", []):
            for comp in obj.get("components", []):
                comp_type = comp.get("type", "")
                if not comp_type:
                    continue
                if "Collider" in comp_type or "Collision" in comp_type:
                    continue
                if comp_type.lower() in _IGNORED:
                    continue

                if comp_type.endswith("Controller"):
                    base = comp_type[:-len("Controller")]
                else:
                    base = comp_type
                script_name = re.sub(r'(?<!^)(?=[A-Z])', '_', base).lower()
                script_name = script_name.replace("__", "_").strip("_")
                if not script_name or script_name in seen:
                    continue
                seen.add(script_name)

                script_path = os.path.join(scripts_dir, f"{script_name}.gd")
                if not os.path.exists(script_path):
                    role = obj.get("role", "")
                    stub = self._generate_script_stub(script_name, role)
                    try:
                        with open(script_path, "w", encoding="utf-8") as f:
                            f.write(stub)
                        self.log_action("script_stub_created", {"script": script_name, "role": role})
                    except Exception:
                        pass

    @staticmethod
    def _generate_script_stub(script_name: str, role: str) -> str:
        """根据脚本名和角色生成功能性存根脚本。"""
        if "pickup" in script_name or "collectible" in script_name or role in ("pickup", "coin"):
            return (
                "extends Area2D\n\n"
                "signal collected(score_value: int)\n\n"
                "@export var score_value: int = 10\n\n"
                "func _ready() -> void:\n"
                "    body_entered.connect(_on_body_entered)\n\n"
                "func _on_body_entered(body: Node2D) -> void:\n"
                "    if body.is_in_group(\"player\") or body.name == \"Player\":\n"
                "        collected.emit(score_value)\n"
                "        queue_free()\n"
            )
        if "score" in script_name or "manager" in script_name and role == "manager":
            return (
                "extends Node\n\n"
                "signal score_changed(new_score: int)\n\n"
                "var score: int = 0\n\n"
                "func add_score(value: int) -> void:\n"
                "    score += value\n"
                "    score_changed.emit(score)\n"
                "    print(\"Score: \", score)\n\n"
                "func get_score() -> int:\n"
                "    return score\n"
            )
        if "enemy" in script_name or role == "enemy":
            return (
                "extends CharacterBody2D\n\n"
                "@export var speed: float = 100.0\n"
                "@export var direction: float = -1.0\n\n"
                "var _gravity: float = ProjectSettings.get_setting(\"physics/2d/default_gravity\")\n\n"
                "func _physics_process(delta: float) -> void:\n"
                "    if not is_on_floor():\n"
                "        velocity.y += _gravity * delta\n"
                "    velocity.x = direction * speed\n"
                "    move_and_slide()\n\n"
                "    if is_on_wall():\n"
                "        direction *= -1.0\n"
            )
        return (
            "extends Node\n\n"
            "func _ready() -> void:\n"
            "    pass\n"
        )

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
                    "name": "Player", "type": "CharacterBody2D", "position": [0, 1, 0],
                    "tag": "Player", "layer": 0, "role": "player",
                    "color": [0.42, 0.55, 1.0, 1.0],
                    "scale": [0.8, 1.0, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"mass": "1", "gravityScale": "2", "freezeRotation": "true", "interpolation": "Interpolate", "collisionDetectionMode": "Continuous"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.9]"}},
                        {"type": "PlayerController", "properties": {"moveSpeed": "5", "jumpForce": "10"}},
                    ],
                },
                {
                    "name": "Ground", "type": "StaticBody2D", "position": [0, -2, 0],
                    "layer": 8, "is_static": True, "role": "ground",
                    "color": [0.20, 0.83, 0.60, 1.0],
                    "scale": [14, 0.5, 1],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[14, 1]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Platform1", "type": "StaticBody2D", "position": [-3, 0, 0], "layer": 8, "is_static": True, "role": "platform",
                    "color": [0.30, 0.69, 0.49, 1.0],
                    "scale": [3, 0.5, 1],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[3, 0.5]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Platform2", "type": "StaticBody2D", "position": [3, 1, 0], "layer": 8, "is_static": True, "role": "platform",
                    "color": [0.30, 0.69, 0.49, 1.0],
                    "scale": [3, 0.5, 1],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[3, 0.5]"}},
                        {"type": "Rigidbody2D", "properties": {"bodyType": "Static"}},
                    ],
                },
                {
                    "name": "Coin1", "type": "Area2D", "position": [-3, 1, 0], "role": "coin",
                    "color": [0.98, 0.75, 0.14, 1.0],
                    "scale": [0.5, 0.5, 0.5],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[0.5, 0.5]", "isTrigger": "true"}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Coin2", "type": "Area2D", "position": [3, 2, 0], "role": "coin",
                    "color": [0.98, 0.75, 0.14, 1.0],
                    "scale": [0.5, 0.5, 0.5],
                    "components": [
                        {"type": "BoxCollider2D", "properties": {"size": "[0.5, 0.5]", "isTrigger": "true"}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "CharacterBody2D", "position": [2, -1, 0], "role": "enemy",
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "3", "freezeRotation": "true"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                        {"type": "EnemyController", "properties": {"moveSpeed": "2", "patrolDistance": "3"}},
                    ],
                },
                {
                    "name": "GameManager", "type": "Node", "position": [0, 0, 0],
                    "role": "manager",
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
                    "name": "Player", "type": "CharacterBody2D", "position": [0, -4, 0],
                    "tag": "Player", "role": "player",
                    "color": [0.42, 0.55, 1.0, 1.0],
                    "scale": [0.8, 1.0, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[1, 1]"}},
                        {"type": "PlayerController", "properties": {}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "CharacterBody2D", "position": [-3, 4, 0], "role": "enemy",
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                    ],
                },
                {
                    "name": "Enemy2", "type": "CharacterBody2D", "position": [3, 5, 0], "role": "enemy",
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 0.8],
                    "components": [
                        {"type": "Rigidbody2D", "properties": {"gravityScale": "0"}},
                        {"type": "BoxCollider2D", "properties": {"size": "[0.8, 0.8]"}},
                    ],
                },
                {
                    "name": "GameBoundary", "type": "Node", "position": [0, 0, 0],
                    "role": "manager",
                    "components": [{"type": "GameManager", "properties": {}}],
                },
                {
                    "name": "EnemySpawner", "type": "Node2D", "position": [0, 6, 0],
                    "role": "spawner",
                    "components": [{"type": "EnemySpawner", "properties": {}}],
                },
            ],
        }

    def _rpg_scene(self) -> Dict:
        """2D 俯视角RPG场景 — 彩色几何体版（无需美术资源）"""
        return {
            "scene_name": "RPGScene",
            "new_scene": True,
            "camera": {
                "position": [0, 0, -10],
                "orthographic": True,
                "orthographic_size": 8,
                "background_color": [0.2, 0.3, 0.2, 1.0],
            },
            "lighting": {"type": "directional", "intensity": 1.0, "rotation": [50, -30, 0]},
            "game_objects": [
                {
                    "name": "Ground", "type": "StaticBody2D", "position": [0, -1, 0],
                    "layer": 8, "is_static": True, "role": "ground",
                    "color": [0.2, 0.5, 0.2, 1.0],
                    "scale": [16, 2, 1],
                    "components": [
                        {"type": "BoxCollision", "properties": {"size": [16, 2]}},
                    ],
                },
                {
                    "name": "Player", "type": "CharacterBody2D", "position": [0, 0, 0],
                    "tag": "Player", "role": "player",
                    "color": [0.42, 0.55, 1.0, 1.0],
                    "scale": [0.8, 0.8, 1],
                    "components": [
                        {"type": "BoxCollision", "properties": {"size": [0.8, 0.8]}},
                        {"type": "PlayerController", "properties": {"moveSpeed": "4"}},
                    ],
                },
                {
                    "name": "MagicCircle", "type": "Area2D", "position": [3, 0, 0],
                    "role": "trigger",
                    "color": [0.6, 0.2, 0.8, 1.0],
                    "scale": [1.5, 1.5, 1],
                    "components": [
                        {"type": "CircleCollision", "properties": {"size": [1.5, 1.5]}},
                    ],
                },
                {
                    "name": "Campfire", "type": "StaticBody2D", "position": [-3, 0, 0],
                    "layer": 8, "is_static": True, "role": "obstacle",
                    "color": [0.8, 0.4, 0.1, 1.0],
                    "scale": [0.5, 0.5, 1],
                    "components": [
                        {"type": "CircleCollision", "properties": {"size": [0.5, 0.5]}},
                    ],
                },
                {
                    "name": "Chest", "type": "Area2D", "position": [2, 1, 0],
                    "role": "pickup",
                    "color": [0.8, 0.6, 0.2, 1.0],
                    "scale": [0.6, 0.6, 1],
                    "components": [
                        {"type": "BoxCollision", "properties": {"size": [0.6, 0.6]}},
                        {"type": "CoinController", "properties": {}},
                    ],
                },
                {
                    "name": "Tree1", "type": "StaticBody2D", "position": [-5, -2, 0],
                    "layer": 8, "is_static": True, "role": "wall",
                    "color": [0.1, 0.4, 0.1, 1.0],
                    "scale": [0.6, 2, 1],
                    "components": [
                        {"type": "CircleCollision", "properties": {"size": [0.6, 0.6]}},
                    ],
                },
                {
                    "name": "Tree2", "type": "StaticBody2D", "position": [5, -2, 0],
                    "layer": 8, "is_static": True, "role": "wall",
                    "color": [0.1, 0.4, 0.1, 1.0],
                    "scale": [0.6, 2, 1],
                    "components": [
                        {"type": "CircleCollision", "properties": {"size": [0.6, 0.6]}},
                    ],
                },
                {
                    "name": "Enemy1", "type": "CharacterBody2D", "position": [4, 1, 0], "role": "enemy",
                    "color": [0.97, 0.44, 0.44, 1.0],
                    "scale": [0.8, 0.8, 1],
                    "components": [
                        {"type": "BoxCollision", "properties": {"size": [0.8, 0.8]}},
                        {"type": "EnemyController", "properties": {"moveSpeed": "2", "patrolDistance": "3"}},
                    ],
                },
                {
                    "name": "BattleManager", "type": "Node", "position": [0, 0, 0],
                    "role": "manager",
                    "components": [{"type": "GameManager", "properties": {}}],
                },
            ],
        }

    def _default_system_prompt(self) -> str:
        return """你是Godot场景设计师。根据游戏需求生成场景描述JSON。

JSON格式：
{
  "scene_name": "场景名称",
  "new_scene": true,
  "camera": {"position": [x,y,z], "orthographic": true, "orthographic_size": 6, "background_color": [r,g,b,a]},
  "lighting": {"type": "directional", "intensity": 1.0, "rotation": [x,y,z]},
  "game_objects": [
    {
      "name": "对象名",
      "type": "CharacterBody2D|StaticBody2D|Area2D|RigidBody2D|Sprite2D|Camera2D|Node",
      "role": "player|enemy|ground|wall|platform|coin|pickup|trigger|hazard|camera|manager",
      "position": [x,y,z],
      "rotation": [x,y,z],
      "scale": [x,y,z],
      "tag": "Player",
      "layer": 0,
      "is_static": false,
      "color": [r,g,b,a],
      "components": [
        {"type": "BoxCollision", "properties": {"size": [w, h]}},
        {"type": "PlayerController", "properties": {"moveSpeed": "5"}}
      ]
    }
  ]
}

支持的组件类型：
- 碰撞：BoxCollision（矩形）, CircleCollision（圆形）, CapsuleCollision（胶囊）
- 自定义脚本：直接使用类名如 PlayerController, GameManager, EnemyController, CoinController

颜色系统（color字段，[r,g,b,a] 0-1范围，如[1,0.5,0,1]是橙色）：
- 也可用material字段填预设名：red, green, blue, yellow, orange, purple, pink, cyan, white, black, gray, brown, gold, silver, player, enemy, ground, platform, coin, water, lava, ice, wood, stone, metal
- 不填color/material时，系统会根据对象名自动上色（Player=蓝, Enemy=红, Ground=绿, Coin=金）

规则：
- 只输出JSON，无额外文本
- 坐标使用Godot坐标系（Y轴向上，2D场景Z轴忽略）
- 2D游戏使用role字段指定节点类型，系统自动映射为Godot节点（player→CharacterBody2D, ground→StaticBody2D, coin→Area2D等）
- 物理体由role决定，不需要额外的Rigidbody组件
- 碰撞使用BoxCollision/CircleCollision组件，系统自动创建CollisionShape子节点

层设置（layer字段）：
- 0 = Default（默认）
- 8 = Ground（地面专用层）
- 所有地面、平台、墙壁等可站立的静态物体必须设layer: 8

性能优化：
- 总GameObject数量控制在15个以内
- 地面/平台合并：用1个大BoxCollision覆盖整块地面（如size=[14,1]）
- 静态物体设is_static: true
- 敌人/动态对象控制在3个以内

游戏完整性（非常重要！）：
- 2D平台游戏必须包含：Player（role=player, 带PlayerController）、Ground（role=ground, layer=8）、Platform（role=platform, layer=8）、Enemy（role=enemy, 带EnemyController）、Coin（role=coin, 带CoinController+BoxCollision）、GameManager（role=manager）
- GameManager管理游戏状态（分数、生命、游戏结束），必须有且仅有一个
- 场景必须是一个可玩的完整游戏，不能只是几个散落的对象
"""
