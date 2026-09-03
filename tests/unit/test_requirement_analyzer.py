"""Requirement Analyzer tests."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import AsyncMock, MagicMock

from src.agents.requirement_analyzer import RequirementAnalyzerAgent
from src.core.state.game_state import AgentType


def _make_agent():
    cfg = {
        "llm": {
            "default_model": "stub",
            "models": {
                "requirement_analyzer": {
                    "provider": "mimo",
                    "model": "mimo-v2.5-pro",
                    "temperature": 0.2,
                    "max_tokens": 2048,
                }
            },
        }
    }
    return RequirementAnalyzerAgent(cfg)


def _make_state(requirements="做一个植物大战僵尸的游戏", engine="godot"):
    return {
        "project_context": {
            "requirements": requirements,
            "engine": engine,
        }
    }


def test_analyze_returns_spec():
    agent = _make_agent()
    agent.llm = MagicMock()
    agent.llm.chat = AsyncMock(return_value='{"game": {"genre": "tower_defense", "camera": "2D_top_down", "difficulty": "medium"}, "player": {"hp": 100, "actions": [], "resources": []}, "enemy": {"types": ["zombie"], "wave_system": true}, "mechanics": [], "assets": [], "scenes": []}')

    import asyncio

    async def run():
        return await agent.execute(_make_state())

    result = asyncio.run(run())
    assert result is not None
    assert result.get("game_spec") is not None
    assert result["game_spec"]["game"]["genre"] == "tower_defense"
    assert result["game_spec"]["game"]["camera"] == "2D_top_down"


def test_fallback_spec_when_llm_fails():
    agent = _make_agent()
    agent.llm = MagicMock()
    agent.llm.chat = AsyncMock(side_effect=Exception("LLM error"))

    import asyncio

    async def run():
        return await agent.execute(_make_state("做一个跑酷游戏"))

    result = asyncio.run(run())
    assert result is not None
    assert result.get("game_spec") is not None
    assert result["game_spec"]["game"]["genre"] == "platformer"
    assert result["game_spec"]["requirements"] == "做一个跑酷游戏"


def test_fallback_spec_tower_defense():
    agent = _make_agent()
    agent.llm = MagicMock()
    agent.llm.chat = AsyncMock(side_effect=Exception("LLM error"))

    import asyncio

    async def run():
        return await agent.execute(_make_state("塔防游戏"))

    result = asyncio.run(run())
    assert result.get("game_spec") is not None
    assert result["game_spec"]["game"]["genre"] == "tower_defense"
