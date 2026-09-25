"""LLM stub 模式测试。

stub 模式（GAMEFORGE_LLM_STUB=1 或 llm.stub.enabled）下：
- get_llm_client 返回 StubLLMClient，不发起网络请求
- 任何 chat 调用抛 LLMStubUnavailable，Agent 走模板降级路径
"""
import base64

import pytest

import src.utils.llm_client as llm_mod
from src.utils.llm_client import (
    LLMStubUnavailable,
    StubLLMClient,
    get_llm_client,
    llm_stub_enabled,
)


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """stub 开关影响客户端缓存，测试前后清空。"""
    llm_mod._client_cache.clear()
    yield
    llm_mod._client_cache.clear()


class TestStubEnabled:
    def test_env_true(self, monkeypatch):
        for val in ("1", "true", "yes", "ON"):
            monkeypatch.setenv("GAMEFORGE_LLM_STUB", val)
            assert llm_stub_enabled({}) is True

    def test_env_false_overrides_config(self, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "0")
        assert llm_stub_enabled({"llm": {"stub": {"enabled": True}}}) is False

    def test_config_fallback(self, monkeypatch):
        monkeypatch.delenv("GAMEFORGE_LLM_STUB", raising=False)
        assert llm_stub_enabled({"llm": {"stub": {"enabled": True}}}) is True
        assert llm_stub_enabled({"llm": {}}) is False
        assert llm_stub_enabled(None) is False


class TestStubClient:
    def test_get_llm_client_returns_stub(self, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
        client = get_llm_client({"llm": {}})
        assert isinstance(client, StubLLMClient)
        # 缓存生效：第二次拿到同一实例
        assert get_llm_client({"llm": {}}) is client

    @pytest.mark.asyncio
    async def test_chat_refuses(self, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
        client = get_llm_client({"llm": {}})
        with pytest.raises(LLMStubUnavailable):
            await client.chat([{"role": "user", "content": "hi"}])

    def test_chat_sync_refuses(self, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
        client = get_llm_client({"llm": {}})
        with pytest.raises(LLMStubUnavailable):
            client.chat_sync([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_json_refuses(self, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
        client = get_llm_client({"llm": {}})
        with pytest.raises(LLMStubUnavailable):
            await client.chat_json([{"role": "user", "content": "hi"}])

    def test_real_client_when_disabled(self, monkeypatch):
        monkeypatch.delenv("GAMEFORGE_LLM_STUB", raising=False)
        client = get_llm_client({"llm": {"providers": {}}})
        assert not isinstance(client, StubLLMClient)


class TestAgentDegradation:
    def test_game_designer_falls_back_to_template_gdm(self, monkeypatch):
        """stub 拒绝调用时，game_designer 应回退到模板 GDM 而非崩溃。"""
        import asyncio

        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
        from src.agents.game_designer import GameDesignerAgent

        agent = GameDesignerAgent({"llm": {}})
        gdm = asyncio.run(agent.generate_design_model("一个简单的平台跳跃游戏", "godot"))
        assert gdm is not None
        assert "genre" in gdm or "core_loop" in gdm


class TestMultimodal:
    def test_attach_images_builds_openai_parts(self):
        from src.utils.llm_client import _attach_images

        messages = [
            {"role": "system", "content": "清单"},
            {"role": "user", "content": "审查"},
        ]
        out = _attach_images(messages, [{"data": b"\x89PNG", "mime": "image/png"}])
        assert out[0]["content"] == "清单"  # system 不变
        parts = out[1]["content"]
        assert parts[0] == {"type": "text", "text": "审查"}
        assert parts[1]["type"] == "image_url"
        url = parts[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64encode(b"\x89PNG").decode() in url

    def test_attach_images_requires_user_message(self):
        from src.utils.llm_client import _attach_images

        with pytest.raises(ValueError):
            _attach_images([{"role": "system", "content": "x"}], [{"data": b"1"}])

    @pytest.mark.asyncio
    async def test_stub_chat_with_images_refuses(self, monkeypatch):
        monkeypatch.setenv("GAMEFORGE_LLM_STUB", "1")
        client = get_llm_client({"llm": {}})
        with pytest.raises(LLMStubUnavailable):
            await client.chat_with_images(
                [{"role": "user", "content": "看图"}], [{"data": b"1"}]
            )
