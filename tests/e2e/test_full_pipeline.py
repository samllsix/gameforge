"""端到端测试 - 测试完整的游戏代码生成流程"""

import pytest
import asyncio
from src.core.graph.workflow import create_workflow
from src.core.state.game_state import GameDevState


class FakeLLMClient:
    """模拟LLM客户端 — 根据消息内容返回不同格式的响应"""

    # 预置的 GDM 响应
    _GDM_RESPONSE = """{
            "game_title": "Offline Test Game",
            "genre": "platformer",
            "engine": "unity",
            "camera_mode": "2D side-scroller",
            "core_loop": "move, collect, score",
            "player_actions": ["move", "jump"],
            "win_conditions": ["reach goal"],
            "fail_conditions": ["fall"],
            "main_systems": [{"name": "movement", "description": "player movement", "priority": "high"}],
            "entities": [{"name": "Player", "role": "player", "components": ["Rigidbody2D"]}],
            "scenes": [{"name": "MainScene", "description": "test scene"}],
            "code_modules": [{"module_name": "PlayerController", "responsibility": "movement", "output_files": ["Assets/Scripts/Player/PlayerController.cs"]}],
            "assets_needed": {},
            "input_map": [],
            "tags_layers": {"tags": ["Player"], "layers": []},
            "physics_settings": {}
        }"""

    # 预置的 C# 代码响应
    _CODE_RESPONSE = """```csharp
// file: Assets/Scripts/Core/GameManager.cs
using UnityEngine;

namespace TestGame.Core
{
    public class GameManager : MonoBehaviour
    {
        public static GameManager Instance { get; private set; }

        private void Awake()
        {
            if (Instance != null && Instance != this) { Destroy(gameObject); return; }
            Instance = this;
            DontDestroyOnLoad(gameObject);
        }
    }
}
```"""

    async def chat(self, *args, **kwargs):
        """根据 system prompt 内容判断返回格式"""
        messages = kwargs.get("messages", args[0] if args else [])
        system_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_text = msg.get("content", "")
                break

        # 代码生成相关的 prompt → 返回 C# 代码
        if any(kw in system_text for kw in ["代码生成", "C#", "Unity", "code generator", "PlayerController", "GameManager"]):
            return self._CODE_RESPONSE

        # 设计相关 → 返回 GDM JSON
        if any(kw in system_text for kw in ["Game Design", "游戏设计", "设计模型", "GDM"]):
            return self._GDM_RESPONSE

        # 任务规划相关 → 返回任务列表
        if any(kw in system_text for kw in ["规划", "planner", "任务拆解", "task"]):
            return """[
            {"id": "task_001", "name": "PlayerController", "type": "code", "description": "创建玩家控制器", "priority": 1, "dependencies": [], "status": "pending"},
            {"id": "task_002", "name": "GameManager", "type": "code", "description": "创建游戏管理器", "priority": 2, "dependencies": ["task_001"], "status": "pending"},
            {"id": "task_003", "name": "Tests", "type": "test", "description": "创建测试用例", "priority": 3, "dependencies": ["task_001", "task_002"], "status": "pending"}
        ]"""

        # 测试生成 → 返回测试代码
        if any(kw in system_text for kw in ["测试", "test", "单元测试"]):
            return """```csharp
// file: Assets/Tests/PlayerControllerTests.cs
using UnityEngine.TestTools;
using NUnit.Framework;

public class PlayerControllerTests
{
    [Test]
    public void PlayerController_Exists()
    {
        Assert.IsNotNull(Object.FindObjectOfType<PlayerController>());
    }
}
```"""

        # 调试 → 返回修复
        if any(kw in system_text for kw in ["调试", "debug", "修复", "fix", "error"]):
            return self._CODE_RESPONSE

        # 默认返回 GDM
        return self._GDM_RESPONSE

    async def chat_json(self, *args, **kwargs):
        return {
            "score": 85,
            "passed": True,
            "issues": [],
            "suggestions": [],
            "fixes": [],
            "files": [],
            "needs_refactoring": False,
            "refactored_code": "",
        }


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    fake = FakeLLMClient()
    modules = [
        "src.agents.game_designer",
        "src.agents.planner",
        "src.agents.code_generator",
        "src.agents.code_reviewer",
        "src.agents.test_generator",
        "src.agents.debugger",
        "src.agents.refactor",
        "src.agents.scene_generator",
    ]
    for module_name in modules:
        module = __import__(module_name, fromlist=["get_llm_client"])
        if hasattr(module, "get_llm_client"):
            monkeypatch.setattr(module, "get_llm_client", lambda *args, **kwargs: fake)


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
