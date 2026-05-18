"""GameForge - LLM客户端模块

提供统一的LLM调用接口，支持Mimo等OpenAI兼容API。
使用AsyncOpenAI实现异步非阻塞调用，内置连接池和单例管理。
"""

import os
import json
import re
import time
import random
import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True

    def get_delay(self, attempt: int) -> float:
        delay = min(self.base_delay * (self.exponential_base ** attempt), self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0,
                 half_open_max_calls: int = 3):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self._lock = asyncio.Lock()

    async def can_execute(self) -> bool:
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    return True
                return False
            if self.state == CircuitState.HALF_OPEN:
                return self.success_count < self.half_open_max_calls
            return False

    async def record_success(self):
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
            else:
                self.failure_count = 0

    async def record_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN

    def get_state(self) -> str:
        return self.state.value


class LLMClientPool:
    """LLM客户端连接池 — 全局单例，避免重复创建客户端实例"""

    _instance: Optional["LLMClientPool"] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._clients: Dict[str, AsyncOpenAI] = {}
        self._config_cache: Dict[str, Dict] = {}

    @classmethod
    async def get_instance(cls) -> "LLMClientPool":
        """异步安全的单例获取"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_client(self, base_url: str, api_key: str) -> AsyncOpenAI:
        """获取或创建AsyncOpenAI客户端（带连接复用）

        Args:
            base_url: API基础URL
            api_key: API密钥

        Returns:
            AsyncOpenAI客户端实例
        """
        cache_key = f"{base_url}|{api_key}"
        if cache_key not in self._clients:
            self._clients[cache_key] = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=3,
                timeout=120.0,
            )
        return self._clients[cache_key]

    def clear(self):
        """清理所有客户端"""
        self._clients.clear()


class LLMClient:
    """LLM客户端 — 封装异步OpenAI兼容API调用"""

    def __init__(self, config: Dict[str, Any]):
        """初始化LLM客户端

        Args:
            config: 配置字典，包含llm相关配置
        """
        llm_config = config.get("llm", {})
        self.default_model = llm_config.get("default_model", "mimo-v2.5-pro")
        self.base_url = llm_config.get(
            "base_url",
            os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
        )
        self.api_key = os.getenv("MIMO_API_KEY", "")

        # 重试和熔断配置
        self.retry_config = RetryConfig(
            max_retries=llm_config.get("max_retries", 3),
            base_delay=llm_config.get("base_retry_delay", 1.0),
            max_delay=llm_config.get("max_retry_delay", 60.0),
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=llm_config.get("circuit_breaker_threshold", 5),
            recovery_timeout=llm_config.get("circuit_breaker_recovery", 60.0),
        )

        # 使用同步客户端作为回退（兼容旧调用）
        from openai import OpenAI

        self._sync_client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        # 异步客户端延迟初始化
        self._async_client: Optional[AsyncOpenAI] = None

    async def _get_async_client(self) -> AsyncOpenAI:
        """获取异步客户端（延迟初始化）"""
        if self._async_client is None:
            pool = await LLMClientPool.get_instance()
            self._async_client = pool.get_client(self.base_url, self.api_key)
        return self._async_client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """异步发送聊天请求（带重试和熔断）

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            模型回复文本
        """
        if not await self.circuit_breaker.can_execute():
            raise RuntimeError(f"Circuit breaker OPEN for {self.base_url}. Try again later.")

        last_error = None
        for attempt in range(self.retry_config.max_retries + 1):
            try:
                client = await self._get_async_client()
                response = await client.chat.completions.create(
                    model=model or self.default_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                await self.circuit_breaker.record_success()
                return response.choices[0].message.content
            except Exception as e:
                last_error = e
                await self.circuit_breaker.record_failure()
                if attempt < self.retry_config.max_retries:
                    delay = self.retry_config.get_delay(attempt)
                    await asyncio.sleep(delay)
        raise last_error

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """同步发送聊天请求（带重试，兼容旧代码）

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            模型回复文本
        """
        last_error = None
        for attempt in range(self.retry_config.max_retries + 1):
            try:
                response = self._sync_client.chat.completions.create(
                    model=model or self.default_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content
            except Exception as e:
                last_error = e
                if attempt < self.retry_config.max_retries:
                    delay = self.retry_config.get_delay(attempt)
                    time.sleep(delay)
        raise last_error

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """异步发送聊天请求并解析JSON响应

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度
            max_tokens: 最大token数

        Returns:
            解析后的JSON字典
        """
        response_text = await self.chat(messages, model, temperature, max_tokens)
        return self._extract_json(response_text)

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """从文本中提取JSON"""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1:
            try:
                return json.loads(text[first_brace : last_brace + 1])
            except json.JSONDecodeError:
                pass

        return {"raw_response": text, "parse_error": True}


# 全局客户端缓存（兼容旧的get_llm_client调用）
_client_cache: Dict[str, LLMClient] = {}
_cache_lock = asyncio.Lock()


def get_llm_client(config: Dict[str, Any]) -> LLMClient:
    """获取LLM客户端实例（带缓存）

    Args:
        config: 配置字典

    Returns:
        LLMClient实例
    """
    llm_config = config.get("llm", {})
    base_url = llm_config.get("base_url", os.getenv("MIMO_BASE_URL", ""))
    cache_key = base_url

    if cache_key not in _client_cache:
        _client_cache[cache_key] = LLMClient(config)
    return _client_cache[cache_key]
