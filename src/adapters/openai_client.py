"""GameForge - OpenAI 兼容 LLM 客户端

基于现有 LLMClient 实现 ILLMClient 接口。
支持所有 OpenAI 兼容 API（Mimo, DeepSeek, 智谱, Kimi 等）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from src.adapters.interface import ILLMClient, Observation, Action, ActionType
from src.utils.llm_client import LLMClient, get_llm_client

logger = logging.getLogger("GameForge.adapters.openai")


# Action 类型推断关键词映射
_ACTION_KEYWORDS: Dict[ActionType, List[str]] = {
    ActionType.GENERATE_CODE: ["代码生成", "生成代码", "generate code", "编写代码", "实现功能", "实现代码"],
    ActionType.REVIEW_CODE: ["代码审查", "审查代码", "review code", "代码检查", "代码评审"],
    ActionType.FIX_CODE: ["修复代码", "修复bug", "fix bug", "修复错误", "自动修复"],
    ActionType.PLAN_TASK: ["任务规划", "制定计划", "task plan", "分解任务", "任务分解"],
    ActionType.DESIGN_GAME: ["游戏设计", "game design", "设计模型", "GDM"],
    ActionType.GENERATE_SCENE: ["场景生成", "生成场景", "generate scene", "构建场景"],
    ActionType.GENERATE_TEST: ["测试生成", "生成测试", "generate test", "编写测试"],
    ActionType.REFACTOR: ["重构", "refactor", "优化代码", "代码重构"],
    ActionType.ANALYZE_ERROR: ["分析错误", "错误分析", "analyze error", "调试", "debug"],
}


def _infer_action_type(observation: Observation, response_text: str) -> ActionType:
    """从 observation 和回复文本推断 ActionType"""
    # 1. 先看 agent_type 直接映射
    agent_map = {
        "code_generator": ActionType.GENERATE_CODE,
        "code_reviewer": ActionType.REVIEW_CODE,
        "debugger": ActionType.FIX_CODE,
        "planner": ActionType.PLAN_TASK,
        "game_designer": ActionType.DESIGN_GAME,
        "scene_generator": ActionType.GENERATE_SCENE,
        "test_generator": ActionType.GENERATE_TEST,
        "refactor": ActionType.REFACTOR,
    }
    agent_type = observation.agent_type.lower()
    if agent_type in agent_map:
        return agent_map[agent_type]

    # 2. 关键词匹配
    combined = f"{observation.messages[-1].get('content', '')} {response_text[:200]}".lower()
    for action_type, keywords in _ACTION_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return action_type

    return ActionType.CHAT


def _extract_data_from_response(text: str) -> Dict[str, Any]:
    """尝试从回复中提取 JSON 结构化数据"""
    from src.utils.json_extractor import extract_json_strict
    try:
        return extract_json_strict(text)
    except Exception:
        return {}


class OpenAIClient(ILLMClient):
    """OpenAI 兼容客户端 — 包装现有 LLMClient

    将底层 LLMClient 的 chat/chat_json 调用适配为 Observation -> Action 接口。
    支持所有 OpenAI 兼容 API（Mimo, DeepSeek, 智谱, Kimi 等）。

    用法:
        client = OpenAIClient(config=config, provider="mimo", model="mimo-v2.5-pro")
        action = await client.generate(observation)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        extract_json: bool = False,
    ):
        """初始化 OpenAI 客户端

        Args:
            config: 全局配置字典
            provider: Provider 名称（如 'mimo', 'deepseek'），None 使用默认
            model: 默认模型名，None 使用配置中的值
            extract_json: 是否自动从回复中提取 JSON 到 Action.data
        """
        self._config = config
        self._provider = provider
        self._model = model
        self._extract_json = extract_json
        self._client: Optional[LLMClient] = None

    def _get_client(self) -> LLMClient:
        """延迟初始化底层 LLMClient"""
        if self._client is None:
            self._client = get_llm_client(
                self._config,
                provider=self._provider,
                model=self._model,
            )
        return self._client

    @property
    def backend_name(self) -> str:
        return f"openai({self._provider or 'default'})"

    @property
    def is_available(self) -> bool:
        """检查后端是否可用（API key 是否配置）"""
        try:
            client = self._get_client()
            return bool(client.api_key)
        except Exception:
            return False

    async def generate(self, observation: Observation) -> Action:
        """根据 Observation 生成结构化 Action

        流程:
            1. 从 observation 提取 messages
            2. 调用底层 LLMClient.chat()
            3. 解析回复为 Action
        """
        client = self._get_client()
        llm_config = self._config.get("llm", {}).get("models", {}).get(observation.agent_type, {})

        model = self._model or llm_config.get("model")
        temperature = llm_config.get("temperature", 0.7)
        max_tokens = llm_config.get("max_tokens", 4096)

        raw_response = await client.chat(
            messages=observation.messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 推断 action_type
        action_type = _infer_action_type(observation, raw_response)

        # 提取结构化数据
        data = {}
        if self._extract_json:
            data = _extract_data_from_response(raw_response)

        return Action(
            action_type=action_type,
            content=raw_response,
            data=data,
            raw_response=raw_response,
            model=model or "",
            provider=self._provider or "unknown",
        )

    async def generate_stream(self, observation: Observation):
        """流式生成 — 当前实现为非流式，一次性返回完整 Action

        TODO: 接入 OpenAI streaming API 实现真正的流式输出
        """
        action = await self.generate(observation)
        yield action

    async def generate_json(self, observation: Observation) -> Action:
        """生成并解析 JSON 响应（使用底层 chat_json）

        适用于预期返回 JSON 的场景（planner, reviewer, debugger 等）。
        """
        client = self._get_client()
        llm_config = self._config.get("llm", {}).get("models", {}).get(observation.agent_type, {})

        model = self._model or llm_config.get("model")
        temperature = llm_config.get("temperature", 0.3)
        max_tokens = llm_config.get("max_tokens", 4096)

        result_dict = await client.chat_json(
            messages=observation.messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        raw_response = json.dumps(result_dict, ensure_ascii=False, indent=2)

        return Action(
            action_type=_infer_action_type(observation, raw_response),
            content=raw_response,
            data=result_dict,
            raw_response=raw_response,
            model=model or "",
            provider=self._provider or "unknown",
        )
