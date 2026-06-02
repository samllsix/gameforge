"""测试 Workflow 工作流图"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.graph.workflow import GameDevWorkflow, create_workflow
from src.core.state.game_state import TaskStatus


class TestWorkflowInit:
    def test_init_creates_all_agents(self, sample_config):
        wf = GameDevWorkflow(sample_config)
        assert wf.orchestrator is not None
        assert wf.planner is not None
        assert wf.code_generator is not None
        assert wf.code_reviewer is not None
        assert wf.test_generator is not None
        assert wf.debugger is not None
        assert wf.graph is not None

    def test_workflow_has_nodes(self, sample_config):
        wf = GameDevWorkflow(sample_config)
        nodes = wf.graph.get_graph().nodes
        node_names = {name for name, _ in nodes.items()}
        expected = {"game_designer", "planner", "code_generator", "code_reviewer",
                    "test_generator", "orchestrator", "debugger", "__start__", "__end__"}
        assert expected.issubset(node_names)


class TestWorkflowRouting:
    def test_route_code_task(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["current_task_id"] = "task-001"  # code type task
        state["current_phase"] = "task_assigned"
        route = wf._route_next(state)
        assert route == "code_generator"

    def test_route_test_task(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["current_task_id"] = "task-003"  # test type task
        state["current_phase"] = "task_assigned"
        route = wf._route_next(state)
        assert route == "test_generator"

    def test_route_complete(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["current_phase"] = "workflow_complete"
        route = wf._route_next(state)
        assert route == "__end__"

    def test_route_no_task_id(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["current_task_id"] = None
        route = wf._route_next(state)
        assert route == "__end__"

    def test_route_nonexistent_task(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["current_task_id"] = "nonexistent"
        route = wf._route_next(state)
        assert route == "__end__"

    def test_route_needs_fix(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["current_phase"] = "needs_fix"
        route = wf._route_next(state)
        assert route == "debugger"


class TestTaskCompletion:
    def test_task_completed_true(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["task_plan"] = [
            {**state["task_plan"][0], "status": TaskStatus.COMPLETED.value},
        ]
        assert wf._is_task_completed(state["task_plan"], "task-001") is True

    def test_task_completed_false(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        assert wf._is_task_completed(sample_game_state["task_plan"], "task-001") is False

    def test_task_completed_nonexistent(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        assert wf._is_task_completed(sample_game_state["task_plan"], "xyz") is False


class TestOrchestratorNode:
    @pytest.mark.asyncio
    async def test_orchestrator_assigns_next_task(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        result = await wf._orchestrator_node(sample_game_state)
        assert result["current_task_id"] == "task-001"
        assert result["current_phase"] == "task_assigned"

    @pytest.mark.asyncio
    async def test_orchestrator_all_complete(self, sample_config, completed_state):
        wf = GameDevWorkflow(sample_config)
        result = await wf._orchestrator_node(completed_state)
        assert result["is_complete"] is True
        assert result["current_phase"] == "workflow_complete"

    @pytest.mark.asyncio
    async def test_orchestrator_routes_to_debugger_on_error(self, sample_config, sample_game_state):
        wf = GameDevWorkflow(sample_config)
        state = sample_game_state.copy()
        state["error_log"] = ["CS0246: type not found"]
        state["current_phase"] = "error"
        result = await wf._orchestrator_node(state)
        assert result["current_phase"] == "needs_fix"
        assert result["fix_attempts"] == 1


class TestCreateWorkflow:
    def test_create_workflow_returns_instance(self, sample_config):
        wf = create_workflow(sample_config)
        assert isinstance(wf, GameDevWorkflow)
