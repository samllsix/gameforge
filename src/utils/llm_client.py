"""GameForge - LLM客户端模块

提供统一的LLM调用接口，支持Mimo等OpenAI兼容API。
使用AsyncOpenAI实现异步非阻塞调用，内置连接池和单例管理。
"""

import asyncio
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

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
        """计算第 attempt 次重试前的等待秒数（指数退避 + 可选抖动）。

        Args:
            attempt: 从 0 开始的重试序号。

        Returns:
            等待秒数，上限为 max_delay；开启 jitter 时乘以 [0.5, 1.5) 随机系数。
        """
        delay = min(self.base_delay * (self.exponential_base ** attempt), self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay


class CircuitBreaker:
    """LLM 调用熔断器 — 连续失败达到阈值后短暂断路，避免雪崩。

    状态机：CLOSED（正常）→ OPEN（断路）→ HALF_OPEN（试探）→ CLOSED。
    """

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
        """检查当前是否允许发起 LLM 调用。

        OPEN 状态且已过 recovery_timeout 时自动转入 HALF_OPEN 放行试探调用；
        HALF_OPEN 状态只放行 half_open_max_calls 个试探。
        """
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
        """记录一次成功调用。

        HALF_OPEN 试探成功累计达标后转回 CLOSED；其他状态清零失败计数。
        """
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
            else:
                self.failure_count = 0

    async def record_failure(self):
        """记录一次失败调用，连续失败达到阈值时打开熔断器。"""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.failure_count = 0

    def get_state(self) -> str:
        """返回当前熔断状态的枚举值（closed / open / half_open）。"""
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

    TEMPERATURE_ONE_MODELS = {"kimi-k2.6"}

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
        self.api_key = provider_api_key or os.getenv(
            llm_config.get("api_key_env", "MIMO_API_KEY"), ""
        )

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

            # 显式超时：OpenAI SDK 默认 600s，同步调用卡住时会长时间挂起；
            # 与连接池中 AsyncOpenAI 的 timeout=120.0 保持一致
            self._sync_client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120.0,
            )
        return self._sync_client

    def _normalize_temperature(self, model: str, temperature: float) -> float:
        """Apply model-specific request constraints without changing whole providers."""
        if model.lower() in self.TEMPERATURE_ONE_MODELS:
            return 1
        return temperature

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
        _provider = self.provider_name
        _model = model or self.default_model
        temperature = self._normalize_temperature(_model, temperature)
        _cacheable = self.cache_enabled and temperature == 0

        if _cacheable:
            from src.utils.redis_client import cache_get, make_cache_key
            cache_key = make_cache_key(
                "llm",
                model=_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            cached = await cache_get(cache_key)
            if cached is not None:
                logger.debug(f"缓存命中: {cache_key[:32]}...")
                try:
                    from src.utils.metrics import record_llm_call
                    record_llm_call(
                        provider=self.base_url.split("//")[1].split(".")[0] if "//" in self.base_url else "unknown",
                        model=_model,
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

                if _cacheable:
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

    async def chat_with_images(
        self,
        messages: List[Dict[str, Any]],
        images: List[Any],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """多模态聊天（视觉审查用）。

        Args:
            messages: 文本消息列表（system + user），图片附加到最后一条 user 消息
            images: 图片列表，元素为 ``{"data": bytes, "mime": "image/png"}``
                    或 ``(bytes, "image/png")`` 元组
            其余参数同 :meth:`chat`

        Returns:
            模型回复文本
        """
        converted = _attach_images(messages, images)
        return await self.chat(converted, model=model, temperature=temperature, max_tokens=max_tokens)

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
        _model = model or self.default_model
        temperature = self._normalize_temperature(_model, temperature)
        last_error = None
        for attempt in range(self.retry_config.max_retries + 1):
            try:
                client = self._get_sync_client()
                response = client.chat.completions.create(
                    model=_model,
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
        """从文本中提取JSON（委托给统一提取器）"""
        from src.utils.json_extractor import extract_json_strict
        return extract_json_strict(text)


# 全局客户端缓存（兼容旧的get_llm_client调用）
_client_cache: Dict[str, LLMClient] = {}
_cache_lock = threading.Lock()


class LLMStubUnavailable(RuntimeError):
    """stub 模式下拒绝真实 LLM 调用。

    Agent 捕获到该异常后应走各自的模板/规则降级路径
    （与线上 API 故障时的降级路径完全一致）。
    """


class StubLLMClient:
    """离线 stub 客户端 — 全链路冒烟用（借鉴 GameFactory-3A 的 CPU-only harness）。

    通过环境变量 ``GAMEFORGE_LLM_STUB=1`` 或配置 ``llm.stub.enabled: true`` 启用。
    与 LLMClient 同接口；任何 chat 调用立即抛 :class:`LLMStubUnavailable`，
    不重试、不熔断、不缓存、不发起网络请求，让整条流水线在
    无网络、无 API key 的机器上确定性跑通。
    """

    provider_name = "stub"
    base_url = "stub://offline"
    api_key = ""
    cache_enabled = False
    cache_ttl = 0

    def __init__(self, config: Optional[Dict[str, Any]] = None, **kwargs: Any):
        llm_config = (config or {}).get("llm", {})
        self.default_model = (
            kwargs.get("default_model")
            or llm_config.get("default_model", "stub-model")
        )
        # 与 LLMClient 同形，但永不重试
        self.retry_config = RetryConfig(max_retries=0, base_delay=0.0, max_delay=0.0)
        self.circuit_breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)

    @staticmethod
    def _refuse() -> None:
        raise LLMStubUnavailable(
            "LLM stub 模式已启用（GAMEFORGE_LLM_STUB=1 或 llm.stub.enabled），"
            "所有 LLM 调用被拒绝；Agent 应走模板降级路径。"
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        self._refuse()

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        self._refuse()

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        self._refuse()

    async def chat_with_images(
        self,
        messages: List[Dict[str, Any]],
        images: List[Any],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        self._refuse()


def llm_stub_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """是否启用 LLM stub 模式。

    优先级：环境变量 ``GAMEFORGE_LLM_STUB`` > 配置 ``llm.stub.enabled``。
    环境变量支持 1/true/yes/on 与 0/false/no/off；未设置时回落到配置。
    """
    env = os.getenv("GAMEFORGE_LLM_STUB", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    stub_cfg = ((config or {}).get("llm", {}) or {}).get("stub", {})
    return bool(stub_cfg.get("enabled", False))


def _attach_images(
    messages: List[Dict[str, Any]], images: List[Any]
) -> List[Dict[str, Any]]:
    """把图片以 OpenAI 兼容的 content parts 形式附加到最后一条 user 消息。

    Zhipu GLM-4V / StepFun 等视觉模型均接受该格式。
    """
    import base64

    if not images:
        return [dict(m) for m in messages]
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("chat_with_images 需要至少一条 user 消息承载图片")

    parts: List[Dict[str, Any]] = [
        {"type": "text", "text": str(messages[-1].get("content", ""))}
    ]
    for img in images:
        if isinstance(img, dict):
            data, mime = img.get("data", b""), img.get("mime", "image/png")
        else:
            data, mime = img[0], (img[1] if len(img) > 1 else "image/png")
        b64 = base64.b64encode(data).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    out = [dict(m) for m in messages[:-1]]
    out.append({**messages[-1], "content": parts})
    return out


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
    api_key = os.getenv(llm_config.get("api_key_env", "MIMO_API_KEY"), "")
    return base_url, api_key


def get_llm_client(config: Dict[str, Any],
                   provider: Optional[str] = None,
                   model: Optional[str] = None):
    """获取LLM客户端实例（带缓存，线程安全，支持多Provider）

    stub 模式（GAMEFORGE_LLM_STUB=1 或 llm.stub.enabled）下返回
    StubLLMClient，全链路离线冒烟用。

    Args:
        config: 配置字典
        provider: Provider名称（如 'mimo', 'deepseek'），None时使用默认
        model: 默认模型名，None时使用配置中的值

    Returns:
        LLMClient 实例（或 stub 模式下的 StubLLMClient）
    """
    if llm_stub_enabled(config):
        with _cache_lock:
            if "stub" not in _client_cache:
                _client_cache["stub"] = StubLLMClient(config)
            return _client_cache["stub"]

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
