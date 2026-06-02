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
import threading
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv
from openai import AsyncOpenAI

logger = logging.getLogger("GameForge.llm")

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
                self.failure_count = 0

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
    """LLM客户端 — 封装异步OpenAI兼容API调用，支持多Provider路由"""

    def __init__(self, config: Dict[str, Any],
                 provider_base_url: Optional[str] = None,
                 provider_api_key: Optional[str] = None,
                 default_model: Optional[str] = None,
                 provider_name: Optional[str] = None):
        """初始化LLM客户端

        Args:
            config: 配置字典，包含llm相关配置（重试、熔断等）
            provider_base_url: Provider的API地址（优先于config中的base_url）
            provider_api_key: Provider的API密钥（优先于环境变量MIMO_API_KEY）
            default_model: 默认模型名（优先于config中的default_model）
            provider_name: Provider名称（用于指标标签，如 'mimo', 'deepseek'）
        """
        llm_config = config.get("llm", {})
        self.default_model = default_model or llm_config.get("default_model", "mimo-v2.5-pro")
        self.provider_name = provider_name or "unknown"
        self.base_url = provider_base_url or llm_config.get(
            "base_url",
            os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
        )
        self.api_key = provider_api_key or os.getenv("MIMO_API_KEY", "")

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

        # 缓存配置
        cache_config = llm_config.get("cache", {})
        self.cache_enabled = cache_config.get("enabled", False)
        self.cache_ttl = cache_config.get("ttl", 3600)

        self._sync_client = None
        # 异步客户端延迟初始化
        self._async_client: Optional[AsyncOpenAI] = None

    async def _get_async_client(self) -> AsyncOpenAI:
        """获取异步客户端（延迟初始化）"""
        if self._async_client is None:
            pool = await LLMClientPool.get_instance()
            self._async_client = pool.get_client(self.base_url, self.api_key)
        return self._async_client

    def _get_sync_client(self):
        """Lazily create the sync client only for legacy sync calls."""
        if self._sync_client is None:
            from openai import OpenAI

            self._sync_client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._sync_client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """异步发送聊天请求（带重试、熔断和可选缓存）

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            模型回复文本
        """
        # 缓存查找（仅 temperature=0 时启用，确保确定性输出）
        if self.cache_enabled and temperature == 0:
            from src.utils.redis_client import cache_get, make_cache_key
            cache_key = make_cache_key(
                "llm",
                model=model or self.default_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            cached = await cache_get(cache_key)
            if cached is not None:
                logger.debug(f"缓存命中: {cache_key[:32]}...")
                # 记录缓存命中指标
                try:
                    from src.utils.metrics import record_llm_call
                    record_llm_call(
                        provider=self.base_url.split("//")[1].split(".")[0] if "//" in self.base_url else "unknown",
                        model=model or self.default_model,
                        method="chat",
                        duration=0,
                        success=True,
                        cached=True,
                    )
                except Exception:
                    pass
                return cached

        if not await self.circuit_breaker.can_execute():
            raise RuntimeError(f"Circuit breaker OPEN for {self.base_url}. Try again later.")

        _provider = self.provider_name
        _model = model or self.default_model
        _start_time = time.time()
        last_error = None

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                client = await self._get_async_client()
                response = await client.chat.completions.create(
                    model=_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                await self.circuit_breaker.record_success()
                result = response.choices[0].message.content

                # 记录成功指标
                try:
                    from src.utils.metrics import record_llm_call
                    record_llm_call(_provider, _model, "chat", time.time() - _start_time, True)
                except Exception:
                    pass

                # 写入缓存（仅 temperature=0 时）
                if self.cache_enabled and temperature == 0:
                    from src.utils.redis_client import cache_set
                    await cache_set(cache_key, result, ttl=self.cache_ttl)

                return result
            except Exception as e:
                last_error = e
                await self.circuit_breaker.record_failure()
                if attempt < self.retry_config.max_retries:
                    delay = self.retry_config.get_delay(attempt)
                    await asyncio.sleep(delay)

        # 记录失败指标
        try:
            from src.utils.metrics import record_llm_call
            record_llm_call(_provider, _model, "chat", time.time() - _start_time, False)
        except Exception:
            pass

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
                client = self._get_sync_client()
                response = client.chat.completions.create(
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
_cache_lock = threading.Lock()


def _resolve_provider_config(config: Dict[str, Any], provider_name: Optional[str] = None):
    """解析Provider配置，返回 (base_url, api_key, default_model)

    Args:
        config: 全局配置字典
        provider_name: Provider名称（如 'mimo', 'deepseek'），None时使用默认

    Returns:
        (base_url, api_key, default_model) 元组
    """
    llm_config = config.get("llm", {})
    providers = llm_config.get("providers", {})

    if provider_name and provider_name in providers:
        provider = providers[provider_name]
        base_url = provider.get("base_url", "")
        api_key_env = provider.get("api_key_env", "")
        api_key = os.getenv(api_key_env, "")
        return base_url, api_key

    # 回退到默认配置（兼容旧逻辑）
    base_url = llm_config.get("base_url", os.getenv("MIMO_BASE_URL", ""))
    api_key = os.getenv("MIMO_API_KEY", "")
    return base_url, api_key


def get_llm_client(config: Dict[str, Any],
                   provider: Optional[str] = None,
                   model: Optional[str] = None) -> LLMClient:
    """获取LLM客户端实例（带缓存，线程安全，支持多Provider）

    Args:
        config: 配置字典
        provider: Provider名称（如 'mimo', 'deepseek'），None时使用默认
        model: 默认模型名，None时使用配置中的值

    Returns:
        LLMClient实例
    """
    base_url, api_key = _resolve_provider_config(config, provider)
    # 缓存键 = provider + base_url，确保不同provider不会复用同一个客户端
    cache_key = f"{provider or 'default'}|{base_url}"

    with _cache_lock:
        if cache_key not in _client_cache:
            _client_cache[cache_key] = LLMClient(
                config,
                provider_base_url=base_url,
                provider_api_key=api_key,
                default_model=model,
                provider_name=provider or "default",
            )
        return _client_cache[cache_key]
