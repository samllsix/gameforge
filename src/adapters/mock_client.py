"""GameForge - Mock LLM 客户端

用于测试和开发，不依赖真实 LLM API。
支持预设响应、录制回放、延迟模拟等。
"""

from __future__ import annotations

import json
import asyncio
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
from datetime import datetime

from src.adapters.interface import ILLMClient, Observation, Action, ActionType

logger = logging.getLogger("GameForge.adapters.mock")


class LocalMockClient(ILLMClient):
    """Mock LLM 客户端 — 用于单元测试和开发调试

    不调用任何真实 API，返回预设或模板化响应。
    支持:
        - 预设响应队列（按顺序消费）
        - 自定义响应函数（根据 observation 动态生成）
        - 模拟延迟
        - 调用记录（用于断言）

    用法:
        # 预设响应
        client = LocalMockClient(responses=[
            Action(content="generated code", action_type=ActionType.GENERATE_CODE),
        ])
        action = await client.generate(observation)

        # 自定义响应函数
        client = LocalMockClient(response_fn=my_fn)
    """

    def __init__(
        self,
        responses: Optional[List[Action]] = None,
        response_fn: Optional[Callable[[Observation], Action]] = None,
        delay: float = 0.0,
        default_action_type: ActionType = ActionType.CHAT,
    ):
        """初始化 Mock 客户端

        Args:
            responses: 预设响应列表（按顺序消费，耗尽后循环）
            response_fn: 自定义响应函数，接收 Observation 返回 Action
            delay: 模拟延迟（秒）
            default_action_type: 默认 Action 类型
        """
        self._responses = list(responses or [])
        self._response_fn = response_fn
        self._delay = delay
        self._default_action_type = default_action_type
        self._call_index = 0
        self._call_log: List[Observation] = []

    @property
    def backend_name(self) -> str:
        return "mock"

    @property
    def is_available(self) -> bool:
        return True

    @property
    def call_log(self) -> List[Observation]:
        """返回所有调用记录（用于测试断言）"""
        return list(self._call_log)

    @property
    def call_count(self) -> int:
        """返回调用次数"""
        return len(self._call_log)

    def reset(self):
        """重置调用记录和索引"""
        self._call_index = 0
        self._call_log.clear()

    def set_responses(self, responses: List[Action]):
        """替换预设响应列表"""
        self._responses = list(responses)
        self._call_index = 0

    async def generate(self, observation: Observation) -> Action:
        """生成 Mock 响应

        优先级: response_fn > responses 队列 > 默认模板
        """
        self._call_log.append(observation)

        if self._delay > 0:
            await asyncio.sleep(self._delay)

        # 1. 自定义函数优先
        if self._response_fn is not None:
            action = self._response_fn(observation)
            if asyncio.iscoroutine(action):
                action = await action
            return action

        # 2. 预设响应队列
        if self._responses:
            idx = self._call_index % len(self._responses)
            self._call_index += 1
            return self._responses[idx]

        # 3. 默认模板响应
        return self._build_default_response(observation)

    async def generate_stream(self, observation: Observation):
        """流式输出 — Mock 实现一次性返回"""
        action = await self.generate(observation)
        yield action

    def _build_default_response(self, observation: Observation) -> Action:
        """根据 observation 构建默认模板响应"""
        # 从最后一条 user message 提取内容
        user_content = ""
        for msg in reversed(observation.messages):
            if msg.get("role") == "user":
                user_content = msg.get("content", "")
                break

        # 根据 agent_type 生成不同的 mock 内容
        agent_type = observation.agent_type.lower()
        content_templates = {
            "code_generator": f'# Generated GDScript (mock)\nextends Node\n\nfunc _ready():\n\tprint("Hello from mock")\n',
            "code_reviewer": '{"score": 8, "issues": [], "suggestions": ["Mock review: looks good"]}',
            "debugger": '{"root_cause": "Mock: no real error", "fix": "Mock fix applied"}',
            "planner": '{"tasks": [{"id": "task_1", "name": "Mock task", "type": "code"}]}',
            "game_designer": '{"title": "Mock Game", "genre": "platformer", "mechanics": ["jump"]}',
            "scene_generator": '{"nodes": [{"type": "Node2D", "name": "Root"}]}',
            "test_generator": 'func test_mock():\n\tassert_true(true, "Mock test passed")\n',
            "refactor": '{"refactored_code": "// mock refactored", "changes": []}',
        }

        content = content_templates.get(
            agent_type,
            f"Mock response for agent '{observation.agent_type}': {user_content[:100]}"
        )

        # 推断 action_type
        agent_action_map = {
            "code_generator": ActionType.GENERATE_CODE,
            "code_reviewer": ActionType.REVIEW_CODE,
            "debugger": ActionType.FIX_CODE,
            "planner": ActionType.PLAN_TASK,
            "game_designer": ActionType.DESIGN_GAME,
            "scene_generator": ActionType.GENERATE_SCENE,
            "test_generator": ActionType.GENERATE_TEST,
            "refactor": ActionType.REFACTOR,
        }
        action_type = agent_action_map.get(agent_type, self._default_action_type)

        return Action(
            action_type=action_type,
            content=content,
            data={},
            raw_response=content,
            model="mock-model",
            provider="mock",
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
