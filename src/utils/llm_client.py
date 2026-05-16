"""GameForge - LLM客户端模块

提供统一的LLM调用接口，支持DeepSeek等OpenAI兼容API。
"""

import os
import json
import re
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLMClient:
    """LLM客户端 - 封装OpenAI兼容API调用"""

    def __init__(self, config: Dict[str, Any]):
        """初始化LLM客户端

        Args:
            config: 配置字典，包含llm相关配置
        """
        llm_config = config.get("llm", {})
        self.default_model = llm_config.get("default_model", "deepseek-chat")
        self.base_url = llm_config.get("base_url", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """发送聊天请求

        Args:
            messages: 消息列表，格式 [{"role": "system", "content": "..."}, ...]
            model: 模型名称，默认使用配置中的default_model
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            模型回复文本
        """
        response = self.client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """发送聊天请求并解析JSON响应

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度（JSON模式建议较低）
            max_tokens: 最大token数

        Returns:
            解析后的JSON字典
        """
        response_text = self.chat(messages, model, temperature, max_tokens)
        return self._extract_json(response_text)

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """从文本中提取JSON

        Args:
            text: 可能包含JSON的文本

        Returns:
            解析后的JSON字典
        """
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从markdown代码块中提取
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找第一个 { 到最后一个 }
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace != -1 and last_brace != -1:
            try:
                return json.loads(text[first_brace:last_brace + 1])
            except json.JSONDecodeError:
                pass

        # 都失败了，返回原始文本包装
        return {"raw_response": text, "parse_error": True}


def get_llm_client(config: Dict[str, Any]) -> LLMClient:
    """获取LLM客户端实例

    Args:
        config: 配置字典

    Returns:
        LLMClient实例
    """
    return LLMClient(config)
