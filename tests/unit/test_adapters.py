"""GameForge - AI Model Adapter 单元测试

覆盖: ILLMClient 接口、OpenAIClient、LocalMockClient、工厂函数
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.adapters.interface import ILLMClient, Observation, Action, ActionType
from src.adapters.mock_client import LocalMockClient
from src.adapters.factory import create_client, list_backends, register_backend


# ── Observation / Action 模型测试 ─────────────────────────────

class TestDataModels:
    """测试 Observation 和 Action 数据模型"""

    def test_observation_creation(self):
        obs = Observation(
            messages=[{"role": "user", "content": "生成玩家移动脚本"}],
            agent_type="code_generator",
            task_id="task_001",
        )
        assert obs.agent_type == "code_generator"
        assert obs.task_id == "task_001"
        assert len(obs.messages) == 1
        assert obs.context == {}
        assert obs.metadata == {}

    def test_observation_defaults(self):
        obs = Observation(messages=[{"role": "user", "content": "hello"}])
        assert obs.agent_type == "unknown"
        assert obs.task_id is None

    def test_action_creation(self):
        action = Action(
            action_type=ActionType.GENERATE_CODE,
            content="extends Node\n",
            model="mimo-v2.5-pro",
            provider="mimo",
        )
        assert action.action_type == ActionType.GENERATE_CODE
        assert action.content == "extends Node\n"
        assert action.data == {}
        assert action.token_usage == {}

    def test_action_to_dict(self):
        action = Action(
            action_type=ActionType.PLAN_TASK,
            content='{"tasks": []}',
            data={"tasks": []},
        )
        d = action.to_dict()
        assert isinstance(d, dict)
        assert d["action_type"] == "plan_task"
        assert d["data"] == {"tasks": []}

    def test_action_type_enum(self):
        assert ActionType.GENERATE_CODE.value == "generate_code"
        assert ActionType.REVIEW_CODE.value == "review_code"
        assert ActionType("chat") == ActionType.CHAT

    def test_action_confidence_validation(self):
        action = Action(confidence=0.8)
        assert action.confidence == 0.8

        with pytest.raises(Exception):
            Action(confidence=1.5)  # 超出范围

    def test_observation_serialization(self):
        obs = Observation(
            messages=[{"role": "system", "content": "你是GDScript专家"}],
            agent_type="code_generator",
            context={"current_phase": "code_gen"},
        )
        d = obs.model_dump()
        assert d["agent_type"] == "code_generator"
        assert d["context"]["current_phase"] == "code_gen"


# ── LocalMockClient 测试 ──────────────────────────────────────

class TestLocalMockClient:
    """测试 Mock 客户端"""

    def _make_observation(self, agent_type: str = "code_generator") -> Observation:
        return Observation(
            messages=[{"role": "user", "content": "生成玩家脚本"}],
            agent_type=agent_type,
        )

    @pytest.mark.asyncio
    async def test_default_response(self):
        client = LocalMockClient()
        obs = self._make_observation()
        action = await client.generate(obs)

        assert isinstance(action, Action)
        assert action.provider == "mock"
        assert action.model == "mock-model"
        assert action.action_type == ActionType.GENERATE_CODE
        assert "GDScript" in action.content or "extends" in action.content

    @pytest.mark.asyncio
    async def test_preset_responses(self):
        responses = [
            Action(content="first", action_type=ActionType.PLAN_TASK),
            Action(content="second", action_type=ActionType.GENERATE_CODE),
        ]
        client = LocalMockClient(responses=responses)
        obs = self._make_observation()

        a1 = await client.generate(obs)
        a2 = await client.generate(obs)
        a3 = await client.generate(obs)  # 循环回第一个

        assert a1.content == "first"
        assert a2.content == "second"
        assert a3.content == "first"  # 循环

    @pytest.mark.asyncio
    async def test_custom_response_fn(self):
        def my_fn(obs: Observation) -> Action:
            return Action(
                content=f"custom for {obs.agent_type}",
                action_type=ActionType.CHAT,
            )

        client = LocalMockClient(response_fn=my_fn)
        obs = self._make_observation("planner")
        action = await client.generate(obs)

        assert action.content == "custom for planner"
        assert action.action_type == ActionType.CHAT

    @pytest.mark.asyncio
    async def test_call_log(self):
        client = LocalMockClient()
        obs1 = self._make_observation("code_generator")
        obs2 = self._make_observation("debugger")

        await client.generate(obs1)
        await client.generate(obs2)

        assert client.call_count == 2
        assert client.call_log[0].agent_type == "code_generator"
        assert client.call_log[1].agent_type == "debugger"

    @pytest.mark.asyncio
    async def test_reset(self):
        client = LocalMockClient()
        await client.generate(self._make_observation())
        assert client.call_count == 1

        client.reset()
        assert client.call_count == 0
        assert client._call_index == 0

    @pytest.mark.asyncio
    async def test_set_responses(self):
        client = LocalMockClient(responses=[
            Action(content="old"),
        ])
        await client.generate(self._make_observation())

        client.set_responses([
            Action(content="new1"),
            Action(content="new2"),
        ])
        a = await client.generate(self._make_observation())
        assert a.content == "new1"

    @pytest.mark.asyncio
    async def test_backend_properties(self):
        client = LocalMockClient()
        assert client.backend_name == "mock"
        assert client.is_available is True

    @pytest.mark.asyncio
    async def test_agent_type_mapping(self):
        """测试不同 agent_type 返回正确的默认 Action 类型"""
        client = LocalMockClient()
        expected = {
            "code_generator": ActionType.GENERATE_CODE,
            "code_reviewer": ActionType.REVIEW_CODE,
            "debugger": ActionType.FIX_CODE,
            "planner": ActionType.PLAN_TASK,
            "game_designer": ActionType.DESIGN_GAME,
            "scene_generator": ActionType.GENERATE_SCENE,
            "test_generator": ActionType.GENERATE_TEST,
            "refactor": ActionType.REFACTOR,
            "unknown_agent": ActionType.CHAT,
        }
        for agent_type, expected_type in expected.items():
            obs = self._make_observation(agent_type)
            action = await client.generate(obs)
            assert action.action_type == expected_type, \
                f"agent_type={agent_type}: expected {expected_type}, got {action.action_type}"

    @pytest.mark.asyncio
    async def test_stream_yields_action(self):
        client = LocalMockClient()
        obs = self._make_observation()

        actions = []
        async for action in client.generate_stream(obs):
            actions.append(action)

        assert len(actions) == 1
        assert isinstance(actions[0], Action)

    @pytest.mark.asyncio
    async def test_delay_simulation(self):
        import time
        client = LocalMockClient(delay=0.01)
        obs = self._make_observation()

        start = time.time()
        await client.generate(obs)
        elapsed = time.time() - start

        assert elapsed >= 0.01


# ── 工厂函数测试 ──────────────────────────────────────────────

class TestFactory:
    """测试 create_client 和后端注册"""

    def test_list_backends(self):
        backends = list_backends()
        assert "openai" in backends
        assert "mock" in backends

    def test_create_mock_client(self):
        client = create_client("mock")
        assert isinstance(client, LocalMockClient)
        assert client.backend_name == "mock"

    def test_create_mock_with_kwargs(self):
        client = create_client("mock", delay=0.5)
        assert isinstance(client, LocalMockClient)
        assert client._delay == 0.5

    def test_create_openai_requires_config(self):
        with pytest.raises(ValueError, match="config"):
            create_client("openai")

    def test_create_openai_with_config(self):
        config = {
            "llm": {
                "providers": {
                    "test": {"base_url": "http://test.com/v1", "api_key_env": "TEST_KEY"}
                },
                "models": {},
            }
        }
        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            client = create_client("openai", config=config, provider="test")
            assert client.backend_name == "openai(test)"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="未知后端"):
            create_client("nonexistent_backend")

    def test_register_custom_backend(self):
        class MyClient(ILLMClient):
            @property
            def backend_name(self):
                return "custom"

            @property
            def is_available(self):
                return True

            async def generate(self, observation):
                return Action(content="custom")

            async def generate_stream(self, observation):
                yield Action(content="custom")

        register_backend("custom_test", MyClient)
        client = create_client("custom_test")
        assert client.backend_name == "custom"

    def test_register_non_subclass_raises(self):
        with pytest.raises(TypeError):
            register_backend("bad", str)

    @pytest.mark.asyncio
    async def test_end_to_end_mock(self):
        """端到端测试: create_client -> generate -> Action"""
        client = create_client("mock")
        obs = Observation(
            messages=[
                {"role": "system", "content": "你是GDScript专家"},
                {"role": "user", "content": "创建一个2D平台跳跃角色控制器"},
            ],
            agent_type="code_generator",
            task_id="e2e_test",
        )
        action = await client.generate(obs)

        assert isinstance(action, Action)
        assert action.action_type == ActionType.GENERATE_CODE
        assert len(action.content) > 0
        assert action.provider == "mock"
        assert action.to_dict() is not None
