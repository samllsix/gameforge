"""GameForge - AI Model Adapter 层

提供统一的 LLM 调用接口，将 Observation 映射为结构化 JSON Action。
支持多后端切换（OpenAI / Mock / 自定义实现）。

用法:
    from src.adapters import create_client, Observation, Action

    client = create_client("openai", config=my_config)
    action = await client.generate(observation)
"""

from src.adapters.interface import ILLMClient, Observation, Action
from src.adapters.openai_client import OpenAIClient
from src.adapters.mock_client import LocalMockClient
from src.adapters.factory import create_client, list_backends

__all__ = [
    # 核心接口
    "ILLMClient",
    "Observation",
    "Action",
    # 实现
    "OpenAIClient",
    "LocalMockClient",
    # 工厂
    "create_client",
    "list_backends",
]
