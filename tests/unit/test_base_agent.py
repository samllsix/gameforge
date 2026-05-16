"""测试 BaseAgent 基类"""

import pytest
from unittest.mock import patch, MagicMock
from src.agents.base import BaseAgent
from src.core.state.game_state import AgentType, GameDevState


class ConcreteAgent(BaseAgent):
    """用于测试的具体Agent实现"""
    async def execute(self, state, **kwargs):
        return {"result": "ok", **kwargs}


class TestBaseAgent:
    def test_init(self, sample_config):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        assert agent.agent_type == AgentType.CODE_GENERATOR
        assert agent.config == sample_config

    def test_init_with_agent_config(self, sample_config):
        config = sample_config.copy()
        config["agents"]["code_generator"] = {"supported_engines": ["unity", "unreal"]}
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, config)
        assert agent.agent_config["supported_engines"] == ["unity", "unreal"]

    def test_get_llm_key(self, sample_config):
        agent = ConcreteAgent(AgentType.ORCHESTRATOR, sample_config)
        assert agent._get_llm_key() == "orchestrator"

    def test_get_llm_config_fallback_to_default(self):
        config = {"llm": {"default_model": "gpt-4"}}
        agent = ConcreteAgent(AgentType.DEBUGGER, config)
        # debugger config not found → falls back to default
        assert agent.llm_config == "gpt-4"

    @pytest.mark.asyncio
    async def test_execute(self, sample_config):
        agent = ConcreteAgent(AgentType.PLANNER, sample_config)
        result = await agent.execute({})
        assert result["result"] == "ok"

    @pytest.mark.asyncio
    async def test_execute_with_kwargs(self, sample_config):
        agent = ConcreteAgent(AgentType.PLANNER, sample_config)
        result = await agent.execute({}, extra="data")
        assert result["extra"] == "data"

    def test_format_state_summary_empty(self, sample_config):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        summary = agent.format_state_summary(GameDevState(
            task_plan=[], current_task_id=None, code_generated={},
            code_artifacts=[], test_results=None, test_report=None,
            fix_history=[], fix_attempts=0, current_phase="init",
            is_complete=False, requires_human_input=False,
            project_context={}, error_log=[],
        ))
        assert "无状态信息" in summary

    def test_format_state_summary_with_tasks(self, sample_config, sample_game_state):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        summary = agent.format_state_summary(sample_game_state)
        assert "任务数量" in summary
        assert "3" in summary

    def test_format_state_summary_with_code(self, sample_config):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        state = GameDevState(
            task_plan=[], current_task_id=None,
            code_generated={"test.cs": "code"},
            code_artifacts=[], test_results=None, test_report=None,
            fix_history=[], fix_attempts=0, current_phase="init",
            is_complete=False, requires_human_input=False,
            project_context={}, error_log=[],
        )
        summary = agent.format_state_summary(state)
        assert "已生成文件" in summary

    def test_format_state_summary_with_test_report(self, sample_config):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        state = GameDevState(
            task_plan=[], current_task_id=None, code_generated={},
            code_artifacts=[],
            test_results=None,
            test_report={"success_rate": 0.85},
            fix_history=[], fix_attempts=0, current_phase="init",
            is_complete=False, requires_human_input=False,
            project_context={}, error_log=[],
        )
        summary = agent.format_state_summary(state)
        assert "测试通过率" in summary
        assert "85" in summary

    def test_validate_state_pass(self, sample_config):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        state = GameDevState(
            task_plan=[], current_task_id=None, code_generated={},
            code_artifacts=[], test_results=None, test_report=None,
            fix_history=[], fix_attempts=0, current_phase="init",
            is_complete=False, requires_human_input=False,
            project_context={}, error_log=[],
        )
        assert agent.validate_state(state, ["task_plan", "code_generated"]) is True

    def test_validate_state_fail(self, sample_config):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        state = GameDevState(
            task_plan=[], current_task_id=None, code_generated={},
            code_artifacts=[], test_results=None, test_report=None,
            fix_history=[], fix_attempts=0, current_phase="init",
            is_complete=False, requires_human_input=False,
            project_context={}, error_log=[],
        )
        assert agent.validate_state(state, ["nonexistent_key"]) is False

    def test_update_state(self, sample_config, sample_game_state):
        agent = ConcreteAgent(AgentType.CODE_GENERATOR, sample_config)
        updated = agent.update_state(sample_game_state, {"current_phase": "updated"})
        assert updated["current_phase"] == "updated"
        assert updated["task_plan"] == sample_game_state["task_plan"]

    def test_log_action(self, sample_config):
        agent = ConcreteAgent(AgentType.PLANNER, sample_config)
        agent.log_action("test_action", {"key": "value"})

    def test_log_error(self, sample_config):
        agent = ConcreteAgent(AgentType.PLANNER, sample_config)
        agent.log_error("test_error", {"detail": "something went wrong"})

    def test_get_prompt_template_not_found(self, sample_config):
        agent = ConcreteAgent(AgentType.PLANNER, sample_config)
        result = agent.get_prompt_template("nonexistent_template_xyz")
        assert result == ""
