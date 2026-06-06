"""GameForge - 规划Agent模块

负责将游戏策划文档解析为开发任务。
"""

import json
import os
from typing import Any, Dict, List, Optional
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType, TaskType
from src.utils.llm_client import get_llm_client

# 模板缓存
_templates_cache: Optional[List[Dict[str, Any]]] = None


def _load_templates() -> List[Dict[str, Any]]:
    global _templates_cache
    if _templates_cache is not None:
        return _templates_cache

    templates = []
    template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "config", "templates")
    for name in ["unity_2d_platformer.json", "unity_space_shooter.json", "unity_rpg_turnbased.json"]:
        path = os.path.join(template_dir, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                templates.append(json.load(f))
    _templates_cache = templates
    return templates


def match_template(requirements: str) -> Optional[Dict[str, Any]]:
    """根据需求文本匹配游戏模板（打分制）

    评分规则：
    - 每个匹配的关键词 +1 分
    - 负关键词匹配 -2 分
    - 返回分数最高的模板，分数 <= 0 则不匹配
    """
    req_lower = requirements.lower()
    templates = _load_templates()

    best_tpl = None
    best_score = 0

    for tpl in templates:
        score = 0
        keywords = tpl.get("keywords", [])
        negative_keywords = tpl.get("negative_keywords", [])

        for kw in keywords:
            if kw.lower() in req_lower:
                score += 1

        for nkw in negative_keywords:
            if nkw.lower() in req_lower:
                score -= 2

        if score > best_score:
            best_score = score
            best_tpl = tpl

    return best_tpl if best_score > 0 else None


class PlannerAgent(BaseAgent):
    """规划Agent

    负责：
    - 解析游戏策划文档
    - 拆解为开发任务
    - 生成任务依赖图
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.PLANNER, config)
        self.llm = get_llm_client(config)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("planner_execute")
        task_plan = await self.plan(state)

        if not task_plan:
            self.log_error("no_task_plan_generated")
            return {"error_log": ["Failed to generate task plan"]}

        return {
            "task_plan": task_plan,
            "current_phase": "planning_complete",
        }

    async def plan(self, state: GameDevState) -> List[Dict[str, Any]]:
        requirements = state.get("project_context", {}).get("requirements", "")
        if not requirements:
            requirements = "默认游戏开发任务"

        self.log_action("generate_task_plan", {"requirements": requirements[:100]})

        # 优先使用 Game Design Model 生成任务
        gdm = state.get("game_design_model")
        if gdm:
            self.log_action("using_gdm_for_planning", {"title": gdm.get("game_title", "")})
            tasks = self._plan_from_gdm(gdm)
            if tasks:
                self.log_action("task_plan_generated_from_gdm", {"task_count": len(tasks)})
                return tasks

        # 次优：尝试匹配模板（确定性快速路径）
        tpl = match_template(requirements)
        if tpl:
            self.log_action("template_matched", {"template": tpl["name"]})
            tasks = tpl.get("task_plan", [])
            for t in tasks:
                t.setdefault("status", "pending")
            return tasks

        # 兜底：LLM生成
        engine = state.get("project_context", {}).get("engine", "unity")
        return await self._plan_with_llm(requirements, engine)

    def _plan_from_gdm(self, gdm: Dict[str, Any]) -> List[Dict[str, Any]]:
        """根据 Game Design Model 生成任务计划"""
        tasks = []
        code_modules = gdm.get("code_modules", [])
        entities = gdm.get("entities", [])
        scenes = gdm.get("scenes", [])

        # 构建实体角色映射
        entity_roles = {}
        for ent in entities:
            entity_roles[ent.get("name", "")] = ent.get("role", "environment")

        # 为每个 code_module 创建任务
        for i, module in enumerate(code_modules):
            module_name = module.get("module_name", f"Module_{i+1}")
            dependencies = module.get("dependencies", [])
            dep_ids = [f"task_{self._module_index(code_modules, d):03d}" for d in dependencies if self._module_index(code_modules, d) >= 0]

            # 确定任务类型
            target_objects = module.get("target_game_objects", [])
            task_type = TaskType.CODE.value
            if any(entity_roles.get(obj) == "ui" for obj in target_objects):
                task_type = TaskType.UI.value

            tasks.append({
                "id": f"task_{i+1:03d}",
                "name": f"实现{module_name}",
                "description": module.get("responsibility", f"实现{module_name}模块"),
                "type": task_type,
                "status": "pending",
                "priority": 1 if module.get("priority") == "high" else (3 if module.get("priority") == "low" else 2),
                "dependencies": dep_ids,
                "assigned_agent": "code_generator",
                "output_files": module.get("output_files", [f"Assets/Scripts/{module_name}.cs"]),
                "target_game_objects": target_objects,
                "required_components": module.get("required_components", []),
                "related_system": module.get("related_system", ""),
                "acceptance_criteria": module.get("acceptance_criteria", f"{module_name}编译通过且功能正常"),
            })

        # 添加场景构建任务（如果有场景定义）
        if scenes:
            scene_task_deps = [f"task_{i+1:03d}" for i in range(len(code_modules))]
            tasks.append({
                "id": f"task_{len(code_modules)+1:03d}",
                "name": "构建游戏场景",
                "description": f"根据游戏设计模型构建 {scenes[0].get('scene_name', 'GameScene')} 场景",
                "type": TaskType.SCENE.value,
                "status": "pending",
                "priority": 2,
                "dependencies": scene_task_deps,
                "assigned_agent": "scene_generator",
                "output_files": ["Assets/Scenes/scene_description.json"],
                "target_game_objects": scenes[0].get("required_objects", []),
                "required_components": [],
                "related_system": "scene",
                "acceptance_criteria": "场景描述JSON完整且与代码一致",
            })

        # 添加文档任务
        tasks.append({
            "id": f"task_{len(code_modules)+2:03d}",
            "name": "生成项目文档",
            "description": "生成README、项目设置建议和配置文档",
            "type": TaskType.DOCUMENTATION.value,
            "status": "pending",
            "priority": 3,
            "dependencies": [],
            "assigned_agent": "code_generator",
            "output_files": ["Assets/README_Unity.md", "Assets/ProjectSettings_Suggestions.md"],
            "target_game_objects": [],
            "required_components": [],
            "related_system": "documentation",
            "acceptance_criteria": "文档包含游戏说明、文件列表和配置建议",
        })

        return tasks

    def _module_index(self, modules: List[Dict], module_name: str) -> int:
        """查找模块在列表中的索引"""
        for i, m in enumerate(modules):
            if m.get("module_name") == module_name:
                return i
        return -1

    async def _plan_with_llm(self, requirements: str, engine: str) -> List[Dict[str, Any]]:
        """使用LLM生成任务计划（兜底）"""
        system_prompt = self.get_prompt_template("planner_system")
        user_prompt = f"""请根据以下游戏需求，生成3-6个代码开发任务列表。

游戏引擎: {engine}
需求描述:
{requirements}

要求：
1. 每个任务必须包含 id, name, description, type, priority, dependencies, assigned_agent 字段
2. type 为 "code"/"ui"/"scene"/"config"
3. assigned_agent 为 "code_generator" 或 "scene_generator"
4. 任务之间可以有合理依赖（如 GameManager 先于 UI）

请严格按照系统提示中的JSON格式输出任务列表。"""

        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.4),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )

            if result.get("parse_error"):
                self.log_error("llm_response_parse_error", {"raw": str(result.get("raw_response", ""))[:200]})
                return self._create_sample_task_plan(requirements)

            tasks = result.get("tasks", [])
            if not tasks:
                self.log_error("no_tasks_in_response")
                return self._create_sample_task_plan(requirements)

            normalized = []
            for i, task in enumerate(tasks):
                normalized.append({
                    "id": task.get("id", f"task_{i+1:03d}"),
                    "name": task.get("name", f"Task {i+1}"),
                    "description": task.get("description", ""),
                    "type": task.get("type", TaskType.CODE.value),
                    "status": "pending",
                    "priority": self._parse_priority(task.get("priority", "medium")),
                    "dependencies": task.get("dependencies", []),
                    "assigned_agent": task.get("assigned_agent", AgentType.CODE_GENERATOR.value),
                    "output_files": task.get("output_files", []),
                    "target_game_objects": task.get("target_game_objects", task.get("scene_objects", [])),
                    "required_components": task.get("required_components", []),
                    "related_system": task.get("related_system", ""),
                    "acceptance_criteria": task.get("acceptance_criteria", ""),
                })

            self.log_action("task_plan_generated", {"task_count": len(normalized)})
            return normalized

        except Exception as e:
            self.log_error("planner_llm_error", {"error": str(e)})
            return self._create_sample_task_plan(requirements)

    def _parse_priority(self, priority) -> int:
        if isinstance(priority, int):
            return priority
        mapping = {"high": 1, "medium": 2, "low": 3}
        return mapping.get(str(priority).lower(), 2)

    def _create_sample_task_plan(self, requirements: str) -> List[Dict[str, Any]]:
        return [
            {
                "id": "task_001",
                "name": "创建Player控制器",
                "description": "实现玩家角色的移动、跳跃控制、碰撞检测和动画状态管理",
                "type": TaskType.CODE.value,
                "status": "pending",
                "priority": 1,
                "dependencies": [],
                "assigned_agent": AgentType.CODE_GENERATOR.value,
                "output_files": ["Assets/Scripts/Player/PlayerController.cs"],
                "scene_objects": ["Player"],
                "required_components": ["Rigidbody2D", "BoxCollider2D", "SpriteRenderer"],
            },
            {
                "id": "task_002",
                "name": "创建GameManager、计分和UI系统",
                "description": "实现游戏状态管理器、计分系统、UI界面（HUD、菜单、分数显示）",
                "type": TaskType.CODE.value,
                "status": "pending",
                "priority": 1,
                "dependencies": [],
                "assigned_agent": AgentType.CODE_GENERATOR.value,
                "output_files": ["Assets/Scripts/Core/GameManager.cs", "Assets/Scripts/Core/ScoreManager.cs"],
                "scene_objects": ["GameManager"],
                "required_components": [],
            },
            {
                "id": "task_003",
                "name": "创建敌人和道具系统",
                "description": "实现敌人AI、道具生成、收集逻辑和关卡管理",
                "type": TaskType.CODE.value,
                "status": "pending",
                "priority": 2,
                "dependencies": [],
                "assigned_agent": AgentType.CODE_GENERATOR.value,
                "output_files": ["Assets/Scripts/Enemy/EnemyController.cs", "Assets/Scripts/Collectibles/CoinController.cs"],
                "scene_objects": ["Enemy1", "Coin1", "Coin2"],
                "required_components": ["Rigidbody2D", "BoxCollider2D"],
            },
        ]

    async def parse_design_document(self, document: str) -> Dict[str, Any]:
        """解析游戏策划文档，提取功能模块和依赖关系

        Args:
            document: 策划文档内容

        Returns:
            解析结果，包含features、modules、dependencies
        """
        system_prompt = """你是一个游戏策划文档分析专家。请分析策划文档并提取关键信息。

输出JSON格式：
{
    "title": "项目名称",
    "genre": "游戏类型",
    "features": [
        {
            "name": "功能名称",
            "description": "功能描述",
            "priority": "high|medium|low",
            "complexity": "简单|中等|复杂"
        }
    ],
    "modules": [
        {
            "name": "模块名称",
            "description": "模块职责",
            "dependencies": ["依赖模块1", "依赖模块2"]
        }
    ],
    "dependencies": [
        {
            "from": "模块A",
            "to": "模块B",
            "type": "requires|uses|extends"
        }
    ],
    "estimated_tasks": 6
}"""

        user_prompt = f"""请分析以下游戏策划文档：

{document[:3000]}

请提取功能模块、依赖关系，并估算需要的任务数量。以JSON格式输出。"""

        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.3),
                max_tokens=self.llm_config.get("max_tokens", 2048),
            )

            if result.get("parse_error"):
                self.log_error("parse_design_doc_error")
                return self._fallback_parse_document(document)

            return {
                "title": result.get("title", "未命名项目"),
                "genre": result.get("genre", "unknown"),
                "features": result.get("features", []),
                "modules": result.get("modules", []),
                "dependencies": result.get("dependencies", []),
                "estimated_tasks": result.get("estimated_tasks", 6),
            }

        except Exception as e:
            self.log_error("parse_design_doc_llm_error", {"error": str(e)})
            return self._fallback_parse_document(document)

    def _fallback_parse_document(self, document: str) -> Dict[str, Any]:
        """LLM调用失败时的回退解析"""
        features = []
        keywords = ["玩家", "角色", "敌人", "关卡", "道具", "UI", "菜单", "音效", "特效", "物理", "碰撞", "计分", "存档"]
        for kw in keywords:
            if kw in document:
                features.append({"name": kw, "description": f"包含{kw}相关功能", "priority": "medium", "complexity": "中等"})

        return {
            "title": "未命名项目",
            "genre": "unknown",
            "features": features or [{"name": "核心玩法", "description": "游戏核心玩法", "priority": "high", "complexity": "中等"}],
            "modules": [{"name": "Core", "description": "核心模块", "dependencies": []}],
            "dependencies": [],
            "estimated_tasks": max(len(features), 3),
        }
