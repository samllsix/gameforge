"""P2-5 端到端：lifespan 启动 → 探活 → /health 与 / 透出 llm_configured。"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_root_endpoint_exposes_llm_status(monkeypatch):
    monkeypatch.delenv("__LLM_TEST_KEY__", raising=False)

    # 导入会在 lifespan 之前触发，先 patch 掉 ping 让它返回固定结果
    from src.utils.llm_health import LLMHealthStatus

    ping_result = LLMHealthStatus(
        llm_configured=False,
        ping_ok=None,
        ping_error="not_configured",
        ping_latency_ms=0.0,
        model="mimo-v2.5-pro",
        base_url="https://api.x.com",
    )

    async def fake_ping(config, timeout=5.0):
        return ping_result

    monkeypatch.setattr("src.utils.llm_health.ping", fake_ping)
    # 让 config 走自定义 api_key_env，避免被系统 MIMO_API_KEY 误认
    import src.api.main as main_mod
    monkeypatch.setitem(main_mod.config, "llm", {
        "base_url": "https://api.x.com",
        "default_model": "mimo-v2.5-pro",
        "api_key_env": "__LLM_TEST_KEY__",
    })

    from fastapi.testclient import TestClient
    with TestClient(main_mod.app) as client:
        r_root = client.get("/")
        r_health = client.get("/health")

    assert r_root.status_code == 200
    body = r_root.json()
    assert "llm_configured" in body
    assert body["llm_configured"] is False
    assert body["llm_ping_ok"] is None
    assert body["llm_ping_error"] == "not_configured"

    assert r_health.status_code == 200
    h = r_health.json()
    assert "llm_configured" in h
    assert h["llm_configured"] is False
    assert h["llm_ping_ok"] is None


def test_lifespan_survives_ping_crash(monkeypatch):
    """ping 自身崩溃 → lifespan 不应阻断启动"""
    from fastapi.testclient import TestClient
    import src.api.main as main_mod

    async def boom(config, timeout=5.0):
        raise RuntimeError("ping impl crashed")

    monkeypatch.setattr("src.utils.llm_health.ping", boom)
    monkeypatch.setitem(main_mod.config, "llm", {
        "base_url": "https://api.x.com",
        "default_model": "m",
        "api_key_env": "__LLM_TEST_KEY__",
    })

    with TestClient(main_mod.app) as client:
        r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["llm_configured"] is False
    assert "crashed" in body["llm_ping_error"]