"""P2-5 LLM 启动探活测试。

覆盖：
- 配置不齐 → llm_configured=False，不发请求
- ping 成功 → ping_ok=True
- ping 401/超时/连接失败 → 错误分类正确
- 探活崩溃 → 不影响 lifespan 整体启动
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.llm_health import LLMHealthStatus, check_config, ping


def _clean_env():
    for k in ("MIMO_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)


def test_check_config_reports_not_configured_without_env(monkeypatch):
    _clean_env()
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    status = check_config({"llm": {"base_url": "https://api.example.com", "default_model": "x"}})
    assert status.llm_configured is False
    assert status.ping_ok is None
    assert status.ping_error == ""


def test_check_config_configured_when_all_fields_present(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    status = check_config({
        "llm": {
            "base_url": "https://api.example.com",
            "default_model": "test-model",
            "api_key_env": "MIMO_API_KEY",
        }
    })
    assert status.llm_configured is True
    assert status.model == "test-model"
    assert status.base_url == "https://api.example.com"


def test_check_config_missing_base_url(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    status = check_config({"llm": {"default_model": "test-model"}})
    assert status.llm_configured is False


def test_check_config_missing_api_key(monkeypatch):
    _clean_env()
    status = check_config({
        "llm": {
            "base_url": "https://api.example.com",
            "default_model": "test-model",
        }
    })
    assert status.llm_configured is False


def test_to_dict_contract_has_required_fields():
    """P2-5 契约：dict 必须能被前端直接消费"""
    status = LLMHealthStatus(
        llm_configured=True, ping_ok=True,
        ping_error="", ping_latency_ms=120.5,
        model="mimo-v2.5-pro", base_url="https://api.x.com",
    )
    d = status.to_dict()
    assert set(d.keys()) == {
        "llm_configured", "ping_ok", "ping_error",
        "ping_latency_ms", "model", "base_url", "checked_at",
    }
    assert d["llm_configured"] is True
    assert d["ping_ok"] is True
    assert d["ping_latency_ms"] == 120.5


def test_ping_skipped_when_not_configured(monkeypatch):
    """未配置时不发请求 — 配置层面已经判定，避免 ping 雪崩。

    用一个测试专属的 env 名（__LLM_TEST_KEY__）避免与系统中已有的
    MIMO_API_KEY 等真实 key 冲突。
    """
    monkeypatch.delenv("__LLM_TEST_KEY__", raising=False)
    config = {
        "llm": {
            "base_url": "https://x.com",
            "default_model": "m",
            "api_key_env": "__LLM_TEST_KEY__",  # 测试独占的 env 名
        }
    }
    # 即便 ping 里 get_llm_client 也不会被调
    with patch("src.utils.llm_client.get_llm_client") as fake:
        status = asyncio.run(ping(config, timeout=1))
    assert status.llm_configured is False
    assert status.ping_ok is None
    assert status.ping_error == "not_configured"
    fake.assert_not_called()


def test_ping_ok_on_success(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    config = {
        "llm": {"base_url": "https://api.x.com", "default_model": "m", "api_key_env": "MIMO_API_KEY"}
    }
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(return_value="ok")
    with patch("src.utils.llm_client.get_llm_client", return_value=fake_client):
        status = asyncio.run(ping(config, timeout=2))
    assert status.llm_configured is True
    assert status.ping_ok is True
    assert status.ping_error == ""
    # ping_latency_ms 可能极小（< 1ms），用 >= 0 而非 > 0
    assert status.ping_latency_ms >= 0


def test_ping_401_classified_as_unauthorized(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-bad")
    config = {
        "llm": {"base_url": "https://api.x.com", "default_model": "m", "api_key_env": "MIMO_API_KEY"}
    }
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(side_effect=Exception("HTTP 401 Unauthorized: invalid API key"))
    with patch("src.utils.llm_client.get_llm_client", return_value=fake_client):
        status = asyncio.run(ping(config, timeout=2))
    assert status.ping_ok is False
    assert status.ping_error == "unauthorized"


def test_ping_429_classified_as_rate_limited(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    config = {
        "llm": {"base_url": "https://api.x.com", "default_model": "m", "api_key_env": "MIMO_API_KEY"}
    }
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(side_effect=Exception("429 Rate limit exceeded"))
    with patch("src.utils.llm_client.get_llm_client", return_value=fake_client):
        status = asyncio.run(ping(config, timeout=2))
    assert status.ping_ok is False
    assert status.ping_error == "rate_limited"


def test_ping_connection_refused(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    config = {
        "llm": {"base_url": "https://api.x.com", "default_model": "m", "api_key_env": "MIMO_API_KEY"}
    }
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))
    with patch("src.utils.llm_client.get_llm_client", return_value=fake_client):
        status = asyncio.run(ping(config, timeout=2))
    assert status.ping_ok is False
    assert status.ping_error == "connection_failed"


def test_ping_timeout(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    config = {
        "llm": {"base_url": "https://api.x.com", "default_model": "m", "api_key_env": "MIMO_API_KEY"}
    }

    async def slow_chat(*args, **kwargs):
        await asyncio.sleep(10)  # 远超 timeout

    fake_client = MagicMock()
    fake_client.chat = AsyncMock(side_effect=slow_chat)
    with patch("src.utils.llm_client.get_llm_client", return_value=fake_client):
        status = asyncio.run(ping(config, timeout=0.2))
    assert status.ping_ok is False
    assert status.ping_error == "timeout"


def test_ping_unexpected_exception_classified_by_type(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    config = {
        "llm": {"base_url": "https://api.x.com", "default_model": "m", "api_key_env": "MIMO_API_KEY"}
    }
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(side_effect=ValueError("bad input"))
    with patch("src.utils.llm_client.get_llm_client", return_value=fake_client):
        status = asyncio.run(ping(config, timeout=2))
    assert status.ping_ok is False
    assert status.ping_error == "ValueError"