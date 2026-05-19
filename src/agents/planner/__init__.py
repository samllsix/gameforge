"""GameForge - 规划Agent模块

负责将游戏策划文档解析为开发任务。
"""

import json
from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType, TaskType
from src.utils.llm_client import get_llm_client


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

        engine = state.get("project_context", {}).get("engine", "unity")

        system_prompt = self.get_prompt_template("planner_system")
        user_prompt = f"""请根据以下游戏需求，生成2-3个综合性的代码开发任务列表。

游戏引擎: {engine}
需求描述:
{requirements}

要求：
1. 生成2-3个代码任务（每个任务应包含完整的功能模块，不要太细）
2. 每个任务必须包含 id, name, description, type, priority, dependencies, assigned_agent 字段
3. type 必须为 "code"（测试由系统自动生成，不需要单独创建测试任务）
4. assigned_agent 必须为 "code_generator"
5. 所有任务之间不能有依赖（dependencies为空），以便并行执行

请严格按照系统提示中的JSON格式输出任务列表。"""

        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.7),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )

            if result.get("parse_error"):
                self.log_error("llm_response_parse_error", {"raw": str(result.get("raw_response", ""))[:200]})
                return self._create_sample_task_plan(requirements)

            tasks = result.get("tasks", [])
            if not tasks:
                self.log_error("no_tasks_in_response")
                return self._create_sample_task_plan(requirements)

            # 规范化任务格式
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
