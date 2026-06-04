"""集成测试 - 测试各组件之间的交互"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.core.graph.workflow import GameDevWorkflow, create_workflow
from src.agents.code_generator import CodeGeneratorAgent
from src.agents.orchestrator import OrchestratorAgent
from src.core.state.game_state import TaskStatus


class TestAgentInteraction:
    """测试Orchestrator与CodeGenerator之间的协作流程"""

    @pytest.fixture
    def mock_agents_config(self, sample_config):
        """配置用于集成测试的mock"""
        c = sample_config.copy()
        c["agents"] = {
            "orchestrator": {},
            "planner": {"mock_task_count": 3},
            "code_generator": {"supported_engines": ["unity"]},
            "code_reviewer": {},
            "test_generator": {},
            "debugger": {},
        }
        return c

    @pytest.mark.asyncio
    async def test_full_task_lifecycle(self, mock_agents_config, sample_game_state):
        """测试：任务从pending → code_generated → completed的完整生命周期"""
        wf = GameDevWorkflow(mock_agents_config)

        # Replace agents with mocked versions that don't call LLMs
        wf.planner.plan = AsyncMock(return_value=sample_game_state["task_plan"])
        wf.code_generator.generate = AsyncMock(return_value=[{
            "file_path": "Assets/Scripts/Test.cs",
            "content": "public class Test {}",
            "language": "csharp",
            "engine": "unity",
        }])
        wf.code_reviewer.review = AsyncMock(return_value={"passed": True})
        wf.test_generator.generate = AsyncMock(return_value={
            "Assets/Tests/TestTests.cs": "[Test] public void TestMethod() {}",
        })

        # Run orchestrator node to get first task
        state = sample_game_state.copy()
        orch_result = await wf._orchestrator_node(state)
        state.update(orch_result)

        assert state["current_task_id"] == "task-001"
        assert state["current_phase"] == "task_assigned"

        # Run code generator
        gen_result = await wf._code_generator_node(state)
        state.update(gen_result)

        assert "Assets/Scripts/Test.cs" in state["code_generated"]
        assert state["current_phase"] == "code_generated"

    @pytest.mark.asyncio
    async def test_dependency_resolution(self, mock_agents_config, sample_game_state):
        """测试任务依赖解析"""
        wf = GameDevWorkflow(mock_agents_config)
        
        # Create custom task plan with dependencies
        task_plan = [
            {
                "id": "A", "name": "Base", "description": "Base system",
                "type": "code", "status": TaskStatus.PENDING.value,
                "priority": 1, "dependencies": [],
                "assigned_agent": "code_generator",
            },
            {
                "id": "B", "name": "Feature", "description": "Feature on top of Base",
                "type": "code", "status": TaskStatus.PENDING.value,
                "priority": 2, "dependencies": ["A"],
                "assigned_agent": "code_generator",
            },
        ]

        state = {**sample_game_state, "task_plan": task_plan}

        # First call should return task A (no deps)
        result = await wf._orchestrator_node(state)
        assert result["current_task_id"] == "A"

        # Complete task A
        state.update(result)
        for t in state["task_plan"]:
            if t["id"] == "A":
                t["status"] = TaskStatus.COMPLETED.value

        # Next call should return task B (dep on A met)
        result = await wf._orchestrator_node(state)
        assert result["current_task_id"] == "B"

    @pytest.mark.asyncio
    async def test_empty_plan_graceful(self, mock_agents_config, sample_game_state):
        """测试空任务计划的优雅处理"""
        wf = GameDevWorkflow(mock_agents_config)
        state = {**sample_game_state, "task_plan": []}

        result = await wf._orchestrator_node(state)
        assert result["is_complete"] is True

    @pytest.mark.asyncio
    async def test_workflow_run(self, mock_agents_config):
        """测试完整workflow.run()"""
        from unittest.mock import AsyncMock, MagicMock

        # 创建通用 mock LLM —— 返回合理的默认值
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value='{"game_title": "Test", "genre": "platformer", "engine": "unity", "camera_mode": "2D", "core_loop": "move", "player_actions": ["move"], "win_conditions": [], "fail_conditions": [], "main_systems": [], "entities": [], "scenes": [], "code_modules": [], "assets_needed": {}, "input_map": [], "tags_layers": {"tags": [], "layers": []}, "physics_settings": {}}')
        mock_llm.chat_json = AsyncMock(return_value={"score": 85, "passed": True, "issues": [], "suggestions": []})

        # Patch get_llm_client 让所有 agent 初始化时拿到 mock
        # 同时 patch 源模块，覆盖函数级 import 的场景（如 scene_generator._generate_via_llm_ir）
        with patch("src.agents.game_designer.get_llm_client", return_value=mock_llm), \
             patch("src.agents.debugger.get_llm_client", return_value=mock_llm), \
             patch("src.agents.refactor.get_llm_client", return_value=mock_llm), \
             patch("src.utils.llm_client.get_llm_client", return_value=mock_llm):

            wf = GameDevWorkflow(mock_agents_config)

            wf.planner.plan = AsyncMock(return_value=[
                {
                    "id": "task-1", "name": "Player",
                    "description": "Create player controller",
                    "type": "code", "status": TaskStatus.PENDING.value,
                    "priority": 1, "dependencies": [],
                    "assigned_agent": "code_generator",
                },
            ])
            wf.code_generator.generate = AsyncMock(return_value=[{
                "file_path": "Assets/Scripts/Player.cs",
                "content": "public class Player : MonoBehaviour {}",
                "language": "csharp", "engine": "unity",
            }])
            wf.code_reviewer.review = AsyncMock(return_value={"passed": True})
            wf.test_generator.generate = AsyncMock(return_value={})

            result = await wf.run({
                "project_context": {"engine": "unity", "project_name": "Test"},
                "requirements": "2D game",
            })

            assert "task_plan" in result
            assert result["is_complete"] is True


class TestModuleImports:
    """测试所有模块可以正确导入"""

    def test_import_agents(self):
        from src.agents.orchestrator import OrchestratorAgent
        from src.agents.planner import PlannerAgent
        from src.agents.code_generator import CodeGeneratorAgent
        from src.agents.code_reviewer import CodeReviewerAgent
        from src.agents.test_generator import TestGeneratorAgent
        from src.agents.debugger import DebuggerAgent
        from src.agents.base import BaseAgent
        assert BaseAgent is not None

    def test_import_state(self):
        from src.core.state.game_state import (
            GameDevState, Task, TaskStatus, TaskType, AgentType,
            CodeArtifact, TestResult, TestReport, FixRecord, ProjectContext,
        )
        assert Task is not None

    def test_import_workflow(self):
        from src.core.graph.workflow import GameDevWorkflow, create_workflow
        assert create_workflow is not None

    def test_import_utils(self):
        from src.utils.logger import GameForgeLogger, get_logger, reset_logger
        assert GameForgeLogger is not None
