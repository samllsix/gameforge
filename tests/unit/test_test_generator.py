"""Tests for Unity test generation output paths."""

from src.agents.test_generator import TestGeneratorAgent


class TestTestGeneratorUnityPaths:
    def test_test_path_goes_to_edit_mode_folder(self, sample_config):
        agent = TestGeneratorAgent(sample_config)

        path = agent._test_path_for_source(
            "Assets/Scripts/Player/PlayerController.cs"
        )

        assert path == "Assets/Tests/EditMode/Player/PlayerControllerTests.cs"

    def test_template_includes_system_collections_and_source_namespace(
        self, sample_config
    ):
        agent = TestGeneratorAgent(sample_config)

        content = agent._generate_test_template(
            "Assets/Scripts/Player/PlayerController.cs",
            "namespace GameForge.Player { public class PlayerController : MonoBehaviour {} }",
        )

        assert "using System.Collections;" in content
        assert "using GameForge.Player;" in content

    def test_runtime_source_excludes_tests_and_editor_scripts(self, sample_config):
        agent = TestGeneratorAgent(sample_config)

        assert agent._is_runtime_source("Assets/Scripts/Player/PlayerController.cs")
        assert not agent._is_runtime_source(
            "Assets/Scripts/Player/PlayerControllerTests.cs"
        )
        assert not agent._is_runtime_source("Assets/Tests/EditMode/FooTests.cs")
        assert not agent._is_runtime_source("Assets/Editor/GameForgeHttpServer.cs")
