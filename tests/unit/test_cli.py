"""测试 CLI 模块"""

import pytest
from unittest.mock import patch, mock_open, MagicMock
from click.testing import CliRunner
from src.cli import cli, load_config


class TestLoadConfig:
    def test_load_config_valid_yaml(self):
        yaml_content = "app:\n  version: '0.1.0'\n"
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            config = load_config("fake_path.yaml")
            assert config["app"]["version"] == "0.1.0"

    def test_load_config_empty(self):
        with patch("builtins.open", mock_open(read_data="")):
            config = load_config("fake_path.yaml")
            assert config is None or config == {}

    def test_load_config_with_llm_section(self):
        yaml_content = """
llm:
  default_model: gpt-4
  temperature: 0.7
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            config = load_config("fake_path.yaml")
            assert config["llm"]["default_model"] == "gpt-4"


class TestCLICommands:
    def _mock_deps(self):
        """setup common mocks for CLI commands"""
        return patch("src.cli.create_workflow"), patch("src.cli.load_config")

    def test_cli_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "generate" in result.output
        assert "workflow" in result.output
        assert "status" in result.output

    def test_status_command(self):
        yaml = "app:\n  version: '0.1.0'\n  environment: test\n"
        with patch("builtins.open", mock_open(read_data=yaml)):
            runner = CliRunner()
            result = runner.invoke(cli, ["status"])
            assert "0.1.0" in result.output or result.exit_code == 0

    def test_generate_command_no_input(self):
        """generate命令必须有--input"""
        runner = CliRunner()
        result = runner.invoke(cli, ["generate"])
        assert result.exit_code != 0

    def test_generate_command_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["generate", "--help"])
        assert "--input" in result.output
        assert "--output" in result.output
