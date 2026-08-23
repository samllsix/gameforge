"""P2-5 LLM 启动探活。

策略（按方案 P2-5）：
- lifespan 启动时跑一次轻量 ping（空 prompt + 5 max_tokens）。
- 失败时显式记日志，不阻塞启动（已有模板/硬编码降级链兜底）。
- 把状态写到 app.state.llm_status，让 / 与 /health 透出 llm_configured 字段，
  让前端启动时能提示用户检查 API Key。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()


@dataclass
class LLMHealthStatus:
    """LLM 探活结果

    Fields:
        llm_configured: bool      # base_url+api_key+model 齐备
        ping_ok: Optional[bool]   # 实际 ping 结果（None = 没跑过/被跳过）
        ping_error: str           # ping 失败原因（401 / timeout / ...）
        ping_latency_ms: float    # ping 耗时
        model: str                # 实际用的模型
        base_url: str             # 实际用的 base_url
        checked_at: float         # 时间戳
    """

    llm_configured: bool
    ping_ok: Optional[bool] = None
    ping_error: str = ""
    ping_latency_ms: float = 0.0
    model: str = ""
    base_url: str = ""
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_config(config: Dict[str, Any]) -> LLMHealthStatus:
    """检查 LLM 配置是否齐备（不实际发请求）。

    用途：即使 ping 失败，/health 也能告诉前端是否「根本就没配置」。
    """
    llm_cfg = (config or {}).get("llm", {}) or {}
    base_url = llm_cfg.get("base_url", "") or ""
    model = llm_cfg.get("default_model", "mimo-v2.5-pro") or ""
    api_key_env = llm_cfg.get("api_key_env", "MIMO_API_KEY") or "MIMO_API_KEY"
    api_key = os.getenv(api_key_env, "") or ""

    configured = bool(base_url and model and api_key)
    return LLMHealthStatus(
        llm_configured=configured,
        model=model,
        base_url=base_url,
    )


async def ping(config: Dict[str, Any], timeout: float = 5.0) -> LLMHealthStatus:
    """异步探活 LLM。

    走最小代价的 chat（max_tokens=5，1 段用户消息）。
    401 → ping_ok=False, ping_error='unauthorized'
    超时 → ping_ok=False, ping_error='timeout'
    成功 → ping_ok=True
    """
    from src.utils.llm_client import get_llm_client

    status = check_config(config)
    if not status.llm_configured:
        status.ping_error = "not_configured"
        logger.warning("llm_health.not_configured", model=status.model)
        return status

    t0 = time.monotonic()
    try:
        client = get_llm_client(config)
        await asyncio.wait_for(
            client.chat(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0.0,
            ),
            timeout=timeout,
        )
        status.ping_ok = True
        status.ping_latency_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "llm_health.ping_ok",
            model=status.model,
            latency_ms=round(status.ping_latency_ms, 1),
        )
    except asyncio.TimeoutError:
        status.ping_ok = False
        status.ping_error = "timeout"
        status.ping_latency_ms = (time.monotonic() - t0) * 1000
        logger.warning("llm_health.ping_timeout", timeout_s=timeout)
    except Exception as e:  # noqa: BLE001
        status.ping_ok = False
        status.ping_latency_ms = (time.monotonic() - t0) * 1000
        err = str(e).lower()
        if "401" in err or "unauthorized" in err or "auth" in err:
            status.ping_error = "unauthorized"
        elif "429" in err or "rate" in err:
            status.ping_error = "rate_limited"
        elif "connection" in err or "refused" in err:
            status.ping_error = "connection_failed"
        else:
            status.ping_error = type(e).__name__
        logger.warning(
            "llm_health.ping_failed",
            model=status.model,
            error=status.ping_error,
            detail=str(e)[:200],
        )
    return status


import asyncio  # noqa: E402  放在文件末尾避免与上面 dataclass 装饰器冲突