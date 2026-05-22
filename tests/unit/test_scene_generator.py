"""测试 SceneGeneratorAgent — 场景生成与Unity离线处理"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.agents.scene_generator import SceneGeneratorAgent


@pytest.fixture
def scene_config(sample_config):
    """场景生成器配置"""
    return {
        **sample_config,
        "unity": {
            "http_port": 8765,
            "auto_build_scene": False,
        },
    }


@pytest.fixture
def scene_config_auto_build(sample_config):
    """场景生成器配置 — 自动构建开启"""
    return {
        **sample_config,
        "unity": {
            "http_port": 8765,
            "auto_build_scene": True,
        },
    }


@pytest.fixture
def sample_state():
    """场景生成用的游戏状态"""
    return {
        "project_context": {
            "requirements": "制作一个2D平台跳跃游戏",
            "project_name": "TestGame",
            "engine": "unity",
        },
        "task_plan": [
            {"name": "PlayerController", "description": "玩家控制脚本"},
            {"name": "GameManager", "description": "游戏管理器"},
        ],
        "code_generated": {},
        "scene_status": None,
        "scene_description": None,
        "scene_path": "",
        "scene_error": None,
    }


class TestSceneGeneratorInit:
    def test_default_auto_build_false(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        assert agent.auto_build_scene is False

    def test_auto_build_true(self, scene_config_auto_build):
        agent = SceneGeneratorAgent(scene_config_auto_build)
        assert agent.auto_build_scene is True

    def test_default_auto_build_when_missing(self, sample_config):
        config = {**sample_config, "unity": {"http_port": 8765}}
        agent = SceneGeneratorAgent(config)
        assert agent.auto_build_scene is False


class TestSceneGeneratorSkipped:
    """auto_build_scene=False时跳过Unity构建"""

    @pytest.mark.asyncio
    async def test_skipped_when_auto_build_disabled(self, scene_config, sample_state):
        agent = SceneGeneratorAgent(scene_config)
        # Mock LLM to return a valid scene
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene",
            "game_objects": [{"name": "Player", "type": "Sprite"}],
        })
        agent.unity_client.check_health = AsyncMock(return_value=True)

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "skipped"
        assert result["scene_skip_reason"] == "auto_build_disabled"
        assert result["scene_description"] is not None
        assert result.get("scene_error") is None
        # Should NOT call check_health when auto_build is disabled
        agent.unity_client.check_health.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_unsupported_engine(self, scene_config, sample_state):
        sample_state["project_context"]["engine"] = "unreal"
        agent = SceneGeneratorAgent(scene_config)

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "skipped"
        assert result["scene_skip_reason"] == "unsupported_engine"


class TestSceneGeneratorUnityOffline:
    """auto_build_scene=True但Unity离线"""

    @pytest.mark.asyncio
    async def test_skipped_when_unity_offline(self, scene_config_auto_build, sample_state):
        agent = SceneGeneratorAgent(scene_config_auto_build)
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene",
            "game_objects": [],
        })
        agent.unity_client.check_health = AsyncMock(return_value=False)

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "skipped"
        assert result["scene_skip_reason"] == "unity_http_unavailable"
        assert result["scene_description"] is not None
        assert result.get("scene_error") is None


class TestSceneGeneratorBuilt:
    """auto_build_scene=True且Unity在线"""

    @pytest.mark.asyncio
    async def test_built_when_unity_online(self, scene_config_auto_build, sample_state):
        agent = SceneGeneratorAgent(scene_config_auto_build)
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene",
            "game_objects": [{"name": "Player", "type": "Sprite"}],
        })
        agent.unity_client.check_health = AsyncMock(return_value=True)
        agent.unity_client.import_files = AsyncMock(return_value={"status": "success", "imported": [], "failed": []})
        agent.unity_client.send_scene = AsyncMock(return_value={
            "status": "success",
            "scene_path": "Assets/Scenes/TestScene.unity",
            "object_count": 1,
        })
        agent.unity_client.compile_scripts = AsyncMock(return_value={"status": "success", "errors": [], "warnings": []})

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "built"
        assert result["scene_path"] == "Assets/Scenes/TestScene.unity"
        assert result["object_count"] == 1
        agent.unity_client.send_scene.assert_called_once()
        agent.unity_client.compile_scripts.assert_called_once()

    @pytest.mark.asyncio
    async def test_imports_code_files_when_auto_build(self, scene_config_auto_build, sample_state):
        sample_state["code_generated"] = {
            "Assets/Scripts/Player/PlayerController.cs": "class PlayerController {}",
            "Assets/Scripts/Core/GameManager.cs": "class GameManager {}",
            "Assets/README.md": "# Not a CS file",
        }
        agent = SceneGeneratorAgent(scene_config_auto_build)
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene", "game_objects": [],
        })
        agent.unity_client.check_health = AsyncMock(return_value=True)
        agent.unity_client.import_files = AsyncMock(return_value={"status": "success", "imported": [], "failed": []})
        agent.unity_client.send_scene = AsyncMock(return_value={"status": "success", "scene_path": "", "object_count": 0})
        agent.unity_client.compile_scripts = AsyncMock(return_value={"status": "success", "errors": []})

        await agent.execute(sample_state)

        # Should only import .cs files
        import_call = agent.unity_client.import_files.call_args[0][0]
        assert "Assets/Scripts/Player/PlayerController.cs" in import_call
        assert "Assets/Scripts/Core/GameManager.cs" in import_call
        assert "Assets/README.md" not in import_call

    @pytest.mark.asyncio
    async def test_no_import_when_no_cs_files(self, scene_config_auto_build, sample_state):
        sample_state["code_generated"] = {"Assets/README.md": "# Readme"}
        agent = SceneGeneratorAgent(scene_config_auto_build)
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene", "game_objects": [],
        })
        agent.unity_client.check_health = AsyncMock(return_value=True)
        agent.unity_client.import_files = AsyncMock(return_value={"status": "success"})
        agent.unity_client.send_scene = AsyncMock(return_value={"status": "success", "scene_path": "", "object_count": 0})
        agent.unity_client.compile_scripts = AsyncMock(return_value={"status": "success", "errors": []})

        await agent.execute(sample_state)

        agent.unity_client.import_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_compile_errors_returned(self, scene_config_auto_build, sample_state):
        agent = SceneGeneratorAgent(scene_config_auto_build)
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene", "game_objects": [],
        })
        agent.unity_client.check_health = AsyncMock(return_value=True)
        agent.unity_client.import_files = AsyncMock(return_value={"status": "success"})
        agent.unity_client.send_scene = AsyncMock(return_value={"status": "success", "scene_path": "", "object_count": 0})
        agent.unity_client.compile_scripts = AsyncMock(return_value={
            "status": "error",
            "errors": [{"file": "Player.cs", "line": 10, "code": "CS0246", "message": "Type not found"}],
        })

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "built"  # scene built even with compile errors
        assert result["compile_status"] == "error"
        assert len(result["compile_errors"]) == 1

    @pytest.mark.asyncio
    async def test_error_when_send_fails(self, scene_config_auto_build, sample_state):
        agent = SceneGeneratorAgent(scene_config_auto_build)
        agent._generate_scene_description = AsyncMock(return_value={
            "scene_name": "TestScene",
            "game_objects": [],
        })
        agent.unity_client.check_health = AsyncMock(return_value=True)
        agent.unity_client.import_files = AsyncMock(return_value={"status": "success"})
        agent.unity_client.send_scene = AsyncMock(return_value={
            "status": "error",
            "error": "Unity内部错误",
        })

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "error"
        assert "Unity内部错误" in result["scene_error"]
        assert result["scene_description"] is not None


class TestSceneGeneratorLLMFailure:
    """LLM失败时使用fallback场景"""

    @pytest.mark.asyncio
    async def test_fallback_scene_on_llm_failure(self, scene_config, sample_state):
        agent = SceneGeneratorAgent(scene_config)
        # Simulate LLM failure — _generate_scene_description catches internally
        # and calls _fallback_scene, so mock it to return fallback
        agent._generate_scene_description = AsyncMock(return_value=agent._platformer_scene())

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "skipped"
        assert result["scene_description"] is not None
        assert "game_objects" in result["scene_description"]

    @pytest.mark.asyncio
    async def test_error_when_no_scene_desc(self, scene_config, sample_state):
        agent = SceneGeneratorAgent(scene_config)
        agent._generate_scene_description = AsyncMock(return_value=None)

        result = await agent.execute(sample_state)

        assert result["scene_status"] == "error"
        assert "LLM未能生成" in result["scene_error"]


class TestFallbackScenes:
    def test_platformer_scene_has_required_fields(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        scene = agent._platformer_scene()
        assert "scene_name" in scene
        assert "game_objects" in scene
        assert "camera" in scene
        assert len(scene["game_objects"]) > 0

    def test_space_shooter_scene(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        scene = agent._space_shooter_scene()
        assert "game_objects" in scene
        assert scene["scene_name"] == "SpaceShooterScene"

    def test_rpg_scene(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        scene = agent._rpg_scene()
        assert "game_objects" in scene
        assert scene["scene_name"] == "RPGScene"

    def test_fallback_detects_platformer(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        scene = agent._fallback_scene("制作一个2D平台跳跃游戏", [])
        assert scene["scene_name"] == "GameScene"

    def test_fallback_detects_space_shooter(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        scene = agent._fallback_scene("太空射击游戏", [])
        assert scene["scene_name"] == "SpaceShooterScene"

    def test_fallback_detects_rpg(self, scene_config):
        agent = SceneGeneratorAgent(scene_config)
        scene = agent._fallback_scene("RPG回合制战斗", [])
        assert scene["scene_name"] == "RPGScene"
