"""GameForge - Model Adapter 工厂

提供 create_client() 统一入口，支持按名称切换 LLM 后端。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional, Type

from src.adapters.interface import ILLMClient

logger = logging.getLogger("GameForge.adapters.factory")

# ── 后端注册表 ─────────────────────────────────────────────────

_BACKEND_REGISTRY: Dict[str, Type[ILLMClient]] = {}


def register_backend(name: str, cls: Type[ILLMClient]):
    """注册一个后端实现

    Args:
        name: 后端名称（如 'openai', 'mock'）
        cls: ILLMClient 子类
    """
    if not (isinstance(cls, type) and issubclass(cls, ILLMClient)):
        raise TypeError(f"{cls} 不是 ILLMClient 的子类")
    _BACKEND_REGISTRY[name.lower()] = cls
    logger.debug(f"已注册后端: {name}")


def list_backends() -> List[str]:
    """列出所有已注册的后端名称"""
    _ensure_defaults_registered()
    return list(_BACKEND_REGISTRY.keys())


def _ensure_defaults_registered():
    """确保默认后端已注册（延迟导入避免循环依赖）"""
    if "openai" not in _BACKEND_REGISTRY:
        from src.adapters.openai_client import OpenAIClient
        register_backend("openai", OpenAIClient)

    if "mock" not in _BACKEND_REGISTRY:
        from src.adapters.mock_client import LocalMockClient
        register_backend("mock", LocalMockClient)


def _accepts_config(cls: Type[ILLMClient]) -> bool:
    """判断后端构造函数是否接受 config 关键字参数。

    用签名内省代替旧的 try/except TypeError 试探：
    试探写法会把构造函数内部真实抛出的 TypeError 当成"不接受 config"而吞掉，
    导致客户端被以错误方式静默创建、真实错误被掩盖。
    """
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        # 无法内省（C 扩展等）：保持旧的试探行为
        return True
    # 类未自定义 __init__ 时，inspect 内省到的是 object.__init__ 的
    # (*args, **kwargs)，但实际不接受任何关键字参数
    if getattr(cls, "__init__", None) is object.__init__:
        return False
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "config" and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


# ── 工厂函数 ──────────────────────────────────────────────────

def create_client(
    backend: str = "openai",
    config: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> ILLMClient:
    """创建 LLM 客户端实例

    Args:
        backend: 后端名称 ('openai', 'mock'，或自定义注册的名称)
        config: 全局配置字典（openai 后端必需）
        **kwargs: 传递给具体后端构造函数的额外参数

    Returns:
        ILLMClient 实例

    Raises:
        ValueError: 未知后端名称

    Examples:
        # OpenAI 后端（连接 Mimo）
        client = create_client("openai", config=my_config, provider="mimo")

        # Mock 后端（测试用）
        client = create_client("mock")

        # 自定义后端
        register_backend("local", MyLocalClient)
        client = create_client("local", model_path="/models/llama")
    """
    _ensure_defaults_registered()

    backend_lower = backend.lower()
    if backend_lower not in _BACKEND_REGISTRY:
        available = ", ".join(_BACKEND_REGISTRY.keys())
        raise ValueError(f"未知后端: '{backend}'。可用后端: {available}")

    cls = _BACKEND_REGISTRY[backend_lower]

    # openai 后端需要 config
    if backend_lower == "openai":
        if config is None:
            raise ValueError("openai 后端需要 config 参数")
        return cls(config=config, **kwargs)

    # mock 后端不需要 config
    if backend_lower == "mock":
        return cls(**kwargs)

    # 自定义后端：按签名决定是否传 config（不再用 TypeError 试探，
    # 避免吞掉构造函数内部真正的 TypeError）
    if _accepts_config(cls):
        return cls(config=config, **kwargs)
    return cls(**kwargs)
