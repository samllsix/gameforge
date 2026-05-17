"""端到端测试 - 测试完整的游戏代码生成流程"""

import pytest
import asyncio
from src.core.graph.workflow import create_workflow
from src.core.state.game_state import GameDevState


class TestFullPipeline:
    """完整流程端到端测试"""

    @pytest.fixture
    def config(self):
        return {
            "app": {"name": "GameForge", "version": "0.1.0"},
            "llm": {
                "default_model": "mimo-v2.5-pro",
                "models": {
                    "planner": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.7, "max_tokens": 4096},
                    "code_generator": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.3, "max_tokens": 8192},
                    "code_reviewer": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.2, "max_tokens": 4096},
                    "test_generator": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.4, "max_tokens": 4096},
                    "debugger": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.2, "max_tokens": 4096},
                    "refactor": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.3, "max_tokens": 8192},
                },
            },
            "agents": {
                "orchestrator": {"max_iterations": 10},
                "planner": {"max_tasks": 20},
                "code_generator": {"supported_engines": ["unity", "unreal"]},
                "test_generator": {"coverage_target": 80},
                "debugger": {"max_fix_attempts": 3},
                "refactor": {"quality_threshold": 70},
            },
        }

    @pytest.mark.asyncio
    async def test_workflow_creates_tasks(self, config):
        """测试工作流能创建任务计划"""
        workflow = create_workflow(config)
        result = await workflow.run({
            "project_context": {
                "engine": "unity",
                "project_name": "TestGame",
                "requirements": "创建一个简单的2D平台跳跃游戏",
            },
        })

        assert "task_plan" in result
        assert len(result["task_plan"]) > 0

    @pytest.mark.asyncio
    async def test_workflow_generates_code(self, config):
        """测试工作流能生成代码"""
        workflow = create_workflow(config)
        result = await workflow.run({
            "project_context": {
                "engine": "unity",
                "project_name": "TestGame",
                "requirements": "创建一个简单的玩家控制器",
            },
        })

        assert "code_generated" in result
        assert len(result["code_generated"]) > 0

    @pytest.mark.asyncio
    async def test_workflow_completes(self, config):
        """测试工作流能正常完成"""
        workflow = create_workflow(config)
        result = await workflow.run({
            "project_context": {
                "engine": "unity",
                "project_name": "TestGame",
                "requirements": "创建一个简单的游戏管理器",
            },
        })

        assert "task_plan" in result
        assert "code_generated" in result

    @pytest.mark.asyncio
    async def test_workflow_handles_empty_requirements(self, config):
        """测试工作流处理空需求"""
        workflow = create_workflow(config)
        result = await workflow.run({
            "project_context": {
                "engine": "unity",
                "project_name": "TestGame",
                "requirements": "",
            },
        })

        assert "task_plan" in result


class TestAgentIntegration:
    """Agent集成测试"""

    @pytest.fixture
    def config(self):
        return {
            "llm": {
                "default_model": "mimo-v2.5-pro",
                "models": {
                    "planner": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.7, "max_tokens": 4096},
                    "code_generator": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.3, "max_tokens": 8192},
                    "code_reviewer": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.2, "max_tokens": 4096},
                    "test_generator": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.4, "max_tokens": 4096},
                    "debugger": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.2, "max_tokens": 4096},
                    "refactor": {"provider": "openai", "model": "mimo-v2.5-pro", "temperature": 0.3, "max_tokens": 8192},
                },
            },
            "agents": {
                "code_generator": {"supported_engines": ["unity"]},
                "debugger": {"max_fix_attempts": 3},
                "refactor": {"quality_threshold": 70},
            },
        }

    @pytest.mark.asyncio
    async def test_planner_agent(self, config):
        """测试Planner Agent"""
        from src.agents.planner import PlannerAgent

        agent = PlannerAgent(config)
        state = {
            "project_context": {"requirements": "创建玩家控制器", "engine": "unity"},
            "task_plan": [], "code_generated": {}, "code_artifacts": [],
            "test_results": None, "test_report": None,
            "fix_history": [], "fix_attempts": 0,
            "current_phase": "init", "is_complete": False,
            "requires_human_input": False, "error_log": [],
        }

        tasks = await agent.plan(state)
        assert len(tasks) > 0
        assert all("id" in t for t in tasks)
        assert all("name" in t for t in tasks)

    def test_code_generator_fallback(self, config):
        """测试CodeGenerator回退生成"""
        from src.agents.code_generator import CodeGeneratorAgent

        agent = CodeGeneratorAgent(config)

        player_code = agent._generate_player_code("unity")
        assert len(player_code) == 1
        assert "PlayerController" in player_code[0]["content"]

        manager_code = agent._generate_game_manager_code("unity")
        assert len(manager_code) == 1
        assert "GameManager" in manager_code[0]["content"]

        collision_code = agent._generate_collision_code("unity")
        assert len(collision_code) == 1

        score_code = agent._generate_score_code("unity")
        assert len(score_code) == 1

    def test_refactor_agent_quality_analysis(self, config):
        """测试RefactorAgent代码质量分析"""
        from src.agents.refactor import RefactorAgent

        agent = RefactorAgent(config)

        good_code = '''using UnityEngine;
namespace GameForge.Test
{
    public class GoodCode : MonoBehaviour
    {
        private void Awake() { }
        private void Update() { }
    }
}'''

        result = agent.analyze_code_quality(good_code)
        assert "score" in result
        assert result["score"] > 0

    def test_eval_metrics(self):
        """测试评测指标"""
        from src.eval.metrics import CodeQualityMetrics, run_evaluation

        code_files = {
            "Assets/Scripts/Test.cs": '''using UnityEngine;
namespace Test
{
    public class TestScript : MonoBehaviour
    {
        private void Update() { }
    }
}'''
        }

        report = run_evaluation("test_project", code_files=code_files)
        assert report.overall_score > 0
        assert len(report.metrics) > 0
