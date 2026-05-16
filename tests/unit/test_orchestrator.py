"""测试 OrchestratorAgent"""

import pytest
import asyncio
from src.agents.orchestrator import OrchestratorAgent
from src.core.state.game_state import TaskStatus


class TestOrchestratorAgent:
    def test_init(self, sample_config):
        agent = OrchestratorAgent(sample_config)
        assert agent.agent_type.value == "orchestrator"

    def test_workflow_progress_empty(self, sample_config):
        agent = OrchestratorAgent(sample_config)
        state = {"task_plan": []}
        progress = agent.get_workflow_progress(state)
        assert progress["total"] == 0
        assert progress["progress"] == 0.0

    def test_workflow_progress_all_pending(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        progress = agent.get_workflow_progress(sample_game_state)
        assert progress["total"] == 3
        assert progress["completed"] == 0
        assert progress["pending"] == 3
        assert progress["progress"] == 0.0

    def test_workflow_progress_partial(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        state = sample_game_state.copy()
        state["task_plan"] = [
            {**state["task_plan"][0], "status": TaskStatus.COMPLETED.value},
            {**state["task_plan"][1], "status": TaskStatus.IN_PROGRESS.value},
            state["task_plan"][2],
        ]
        progress = agent.get_workflow_progress(state)
        assert progress["completed"] == 1
        assert progress["in_progress"] == 1
        assert progress["pending"] == 1
        assert progress["progress"] == 1 / 3

    def test_workflow_progress_all_complete(self, sample_config, completed_state):
        agent = OrchestratorAgent(sample_config)
        progress = agent.get_workflow_progress(completed_state)
        assert progress["completed"] == 3
        assert progress["pending"] == 0
        assert progress["progress"] == 1.0

    def test_is_task_completed_true(self, sample_config, completed_state):
        agent = OrchestratorAgent(sample_config)
        assert agent._is_task_completed(completed_state, "task-001") is True

    def test_is_task_completed_false(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        assert agent._is_task_completed(sample_game_state, "task-001") is False

    def test_is_task_completed_nonexistent(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        assert agent._is_task_completed(sample_game_state, "nonexistent") is False

    def test_get_next_task_first(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        next_task = asyncio.run(agent.get_next_task(sample_game_state))
        assert next_task is not None
        assert next_task["task_id"] == "task-001"

    def test_get_next_task_blocked_by_dependency(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        state = sample_game_state.copy()
        state["task_plan"] = [
            {**state["task_plan"][0], "status": TaskStatus.PENDING.value},
            {**state["task_plan"][1], "status": TaskStatus.PENDING.value},
        ]
        next_task = asyncio.run(agent.get_next_task(state))
        assert next_task["task_id"] == "task-001"

    def test_get_next_task_after_first_complete(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        state = sample_game_state.copy()
        state["task_plan"] = [
            {**state["task_plan"][0], "status": TaskStatus.COMPLETED.value},
            {**state["task_plan"][1], "status": TaskStatus.PENDING.value},
        ]
        next_task = asyncio.run(agent.get_next_task(state))
        assert next_task is not None
        assert next_task["task_id"] == "task-002"

    def test_get_next_task_all_done(self, sample_config, completed_state):
        agent = OrchestratorAgent(sample_config)
        next_task = asyncio.run(agent.get_next_task(completed_state))
        assert next_task is None

    def test_get_next_task_empty_plan(self, sample_config):
        agent = OrchestratorAgent(sample_config)
        next_task = asyncio.run(agent.get_next_task({"task_plan": []}))
        assert next_task is None

    def test_get_next_task_skips_in_progress(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        state = sample_game_state.copy()
        state["task_plan"] = [
            {**state["task_plan"][0], "status": TaskStatus.IN_PROGRESS.value},
            {**state["task_plan"][1], "status": TaskStatus.PENDING.value},
        ]
        next_task = asyncio.run(agent.get_next_task(state))
        assert next_task is None

    @pytest.mark.asyncio
    async def test_execute_with_tasks(self, sample_config, sample_game_state):
        agent = OrchestratorAgent(sample_config)
        result = await agent.execute(sample_game_state)
        assert "current_task_id" in result
        assert result["current_task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_execute_no_tasks(self, sample_config):
        agent = OrchestratorAgent(sample_config)
        result = await agent.execute({"task_plan": []})
        assert result["is_complete"] is True
        assert result["current_phase"] == "workflow_complete"
