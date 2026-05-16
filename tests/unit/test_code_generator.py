"""测试 CodeGeneratorAgent"""

import pytest
from src.agents.code_generator import CodeGeneratorAgent
from src.core.state.game_state import TaskType


class TestCodeGeneratorAgent:
    def test_init(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        assert agent.agent_type.value == "code_generator"
        assert "unity" in agent.supported_engines
        assert "unreal" in agent.supported_engines

    def test_generate_player_code_unity(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_player_code("unity")
        assert len(artifacts) == 1
        art = artifacts[0]
        assert art["file_path"] == "Assets/Scripts/Player/PlayerController.cs"
        assert art["language"] == "csharp"
        assert art["engine"] == "unity"
        assert "PlayerController" in art["content"]
        assert "MonoBehaviour" in art["content"]
        assert "_moveSpeed" in art["content"]
        assert "_jumpForce" in art["content"]
        assert "Move()" in art["content"]
        assert "Jump()" in art["content"]

    def test_generate_player_code_unreal(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_player_code("unreal")
        assert len(artifacts) == 1
        art = artifacts[0]
        assert art["file_path"] == "Source/GameForge/Player/PlayerCharacter.cpp"
        assert art["language"] == "cpp"
        assert art["engine"] == "unreal"
        assert "PlayerCharacter" in art["content"]
        assert "CharacterMovementComponent" in art["content"]

    def test_generate_game_manager_code_unity(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_game_manager_code("unity")
        assert len(artifacts) == 1
        art = artifacts[0]
        assert art["file_path"] == "Assets/Scripts/Core/GameManager.cs"
        assert "Singleton" in art["content"]
        assert "GameManager" in art["content"]
        assert "Instance" in art["content"]
        assert "AddScore" in art["content"]
        assert "LoseLife" in art["content"]
        assert "GameOver" in art["content"]
        assert "RestartGame" in art["content"]
        assert "DontDestroyOnLoad" in art["content"]

    def test_generate_game_manager_code_unreal(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_game_manager_code("unreal")
        assert artifacts == []

    def test_generate_collision_code(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_collision_code("unity")
        assert artifacts == []

    def test_generate_score_code(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_score_code("unity")
        assert artifacts == []

    def test_generate_generic_code(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_generic_code("UnknownTask", "unity")
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_generate_player_task(self, sample_config, sample_game_state):
        agent = CodeGeneratorAgent(sample_config)
        task = sample_game_state["task_plan"][0]  # "Player" task
        artifacts = await agent.generate(sample_game_state, task)
        assert len(artifacts) == 1
        assert "Player" in artifacts[0]["file_path"]

    @pytest.mark.asyncio
    async def test_generate_game_manager_task(self, sample_config, sample_game_state):
        agent = CodeGeneratorAgent(sample_config)
        task = sample_game_state["task_plan"][1]  # "GameManager" task
        artifacts = await agent.generate(sample_game_state, task)
        assert len(artifacts) == 1
        assert "GameManager" in artifacts[0]["file_path"]

    @pytest.mark.asyncio
    async def test_generate_non_code_task(self, sample_config, sample_game_state):
        agent = CodeGeneratorAgent(sample_config)
        task = sample_game_state["task_plan"][2]  # Test type task
        artifacts = await agent.generate(sample_game_state, task)
        assert artifacts == []

    def test_player_code_has_required_elements(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_player_code("unity")
        code = artifacts[0]["content"]
        # Check C# conventions
        assert "namespace" in code
        assert "using UnityEngine" in code
        assert "/// <summary>" in code  # XML documentation
        assert "#region" in code
        assert "private" in code  # Proper access modifiers
        assert "[SerializeField]" in code  # Unity attribute
        assert "MonoBehaviour" in code
        assert "Rigidbody2D" in code
        assert "_rb" in code  # Private field naming convention

    def test_game_manager_code_has_required_elements(self, sample_config):
        agent = CodeGeneratorAgent(sample_config)
        artifacts = agent._generate_game_manager_code("unity")
        code = artifacts[0]["content"]
        # Check singleton pattern
        assert "public static GameManager Instance" in code
        assert "Instance == null" in code
        assert "DontDestroyOnLoad" in code
        assert "Destroy(gameObject)" in code
        # Check game state management
        assert "_maxLives" in code
        assert "_currentLives" in code
        assert "_score" in code
        assert "_isGameOver" in code
