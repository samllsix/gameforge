"""测试游戏设计Agent"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.agents.game_designer import GameDesignerAgent


@pytest.fixture
def config():
    return {"model": {"name": "test-model", "temperature": 0.3}}


@pytest.fixture
def agent(config):
    return GameDesignerAgent(config)


@pytest.fixture
def sample_state():
    return {
        "project_context": {
            "requirements": "创建一个2D平台跳跃游戏，玩家可以左右移动和跳跃，有计分系统",
            "engine": "unity",
            "project_name": "TestGame",
        },
        "current_phase": "started",
    }


class TestGameDesignerAgent:
    def test_init(self, agent):
        assert agent is not None
        assert agent.llm is not None

    def test_fallback_gdm_platformer(self, agent):
        """测试关键词匹配生成平台跳跃GDM"""
        gdm = agent._fallback_gdm("创建一个2D平台跳跃游戏", "unity")
        assert gdm["game_title"]
        assert gdm["genre"] == "platformer"
        assert gdm["camera_mode"] == "2D side-scroller"
        assert len(gdm["entities"]) > 0
        assert len(gdm["main_systems"]) > 0

    def test_fallback_gdm_shooter(self, agent):
        """测试关键词匹配生成射击游戏GDM"""
        gdm = agent._fallback_gdm("制作太空射击游戏", "unity")
        assert gdm["genre"] == "shooter"
        assert gdm["camera_mode"] == "2D top-down"

    def test_fallback_gdm_rpg(self, agent):
        """测试关键词匹配生成RPG GDM"""
        gdm = agent._fallback_gdm("开发RPG战斗系统", "unity")
        assert gdm["genre"] == "rpg"
        assert gdm["camera_mode"] == "2D top-down"

    def test_fallback_gdm_generic(self, agent):
        """测试通用fallback GDM"""
        gdm = agent._fallback_gdm("做一个有趣的游戏", "unity")
        assert gdm["game_title"]
        assert gdm["genre"] == "platformer"  # 默认fallback为platformer
        assert "entities" in gdm
        assert "main_systems" in gdm

    def test_normalize_gdm(self, agent):
        """测试GDM标准化补全缺失字段"""
        raw = {"game_title": "Test", "genre": "platformer"}
        normalized = agent._normalize_gdm(raw, "test", "unity")
        assert "camera_mode" in normalized
        assert "core_loop" in normalized
        assert "player_actions" in normalized
        assert "entities" in normalized
        assert "main_systems" in normalized
        assert "code_modules" in normalized
        assert "input_map" in normalized
        assert "tags_layers" in normalized

    def test_normalize_gdm_preserves_existing(self, agent):
        """测试标准化不覆盖已存在的字段"""
        raw = {
            "game_title": "MyGame",
            "genre": "shooter",
            "camera_mode": "first_person",
            "entities": [{"name": "Player", "type": "player"}],
        }
        normalized = agent._normalize_gdm(raw, "test", "unity")
        assert normalized["game_title"] == "MyGame"
        assert normalized["camera_mode"] == "first_person"
        assert len(normalized["entities"]) == 1

    @pytest.mark.asyncio
    async def test_execute_with_llm(self, agent, sample_state):
        """测试LLM生成GDM成功路径"""
        mock_gdm = {
            "game_title": "Platform Jump",
            "genre": "platformer",
            "camera_mode": "2d_side",
            "entities": [{"name": "Player", "type": "player"}],
            "main_systems": [{"name": "Movement", "description": "Player movement"}],
        }
        with patch.object(agent.llm, "chat", new_callable=AsyncMock, return_value='{"game_title": "Platform Jump", "genre": "platformer", "camera_mode": "2d_side", "entities": [{"name": "Player", "type": "player"}], "main_systems": [{"name": "Movement", "description": "Player movement"}]}'):
            result = await agent.execute(sample_state)
            assert "game_design_model" in result
            assert result["game_design_model"]["game_title"] == "Platform Jump"

    @pytest.mark.asyncio
    async def test_execute_fallback_on_llm_error(self, agent, sample_state):
        """测试LLM失败时使用fallback"""
        with patch.object(agent.llm, "chat", new_callable=AsyncMock, side_effect=Exception("API error")):
            result = await agent.execute(sample_state)
            assert "game_design_model" in result
            gdm = result["game_design_model"]
            assert gdm["game_title"]
            assert gdm["genre"]

    @pytest.mark.asyncio
    async def test_execute_fallback_on_invalid_json(self, agent, sample_state):
        """测试LLM返回无效JSON时使用fallback"""
        with patch.object(agent.llm, "chat", new_callable=AsyncMock, return_value="not valid json"):
            result = await agent.execute(sample_state)
            assert "game_design_model" in result
            gdm = result["game_design_model"]
            assert gdm["game_title"]

    def test_gdm_has_code_modules(self, agent):
        """测试GDM包含代码模块定义"""
        gdm = agent._fallback_gdm("2D平台跳跃", "unity")
        assert "code_modules" in gdm
        for module in gdm["code_modules"]:
            assert "module_name" in module
            assert "responsibility" in module

    def test_gdm_has_input_map(self, agent):
        """测试GDM包含输入映射"""
        gdm = agent._fallback_gdm("2D平台跳跃", "unity")
        assert "input_map" in gdm
        for inp in gdm["input_map"]:
            assert "name" in inp
            assert "type" in inp

    def test_gdm_has_tags_layers(self, agent):
        """测试GDM包含Tag/Layer定义"""
        gdm = agent._fallback_gdm("2D平台跳跃", "unity")
        assert "tags_layers" in gdm
        assert "tags" in gdm["tags_layers"]
        assert "layers" in gdm["tags_layers"]
