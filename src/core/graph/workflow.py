"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
"""

from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, END

from src.core.state.game_state import GameDevState, TaskStatus, AgentType
from src.agents.orchestrator import OrchestratorAgent
from src.agents.planner import PlannerAgent
from src.agents.code_generator import CodeGeneratorAgent
from src.agents.code_reviewer import CodeReviewerAgent
from src.agents.test_generator import TestGeneratorAgent
from src.agents.debugger import DebuggerAgent


class GameDevWorkflow:
    """游戏开发工作流管理器"""

    def __init__(self, config: Dict[str, Any]):
        """初始化工作流

        Args:
            config: 工作流配置
        """
        self.config = config
        self.orchestrator = OrchestratorAgent(config)
        self.planner = PlannerAgent(config)
        self.code_generator = CodeGeneratorAgent(config)
        self.code_reviewer = CodeReviewerAgent(config)
        self.test_generator = TestGeneratorAgent(config)
        self.debugger = DebuggerAgent(config)

        self.workflow = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """构建LangGraph状态图

        Returns:
            配置好的状态图
        """
        # 创建状态图
        workflow = StateGraph(GameDevState)

        # 添加节点
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("code_generator", self._code_generator_node)
        workflow.add_node("code_reviewer", self._code_reviewer_node)
        workflow.add_node("test_generator", self._test_generator_node)
        workflow.add_node("orchestrator", self._orchestrator_node)

        # 设置入口点
        workflow.set_entry_point("planner")

        # 添加边
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("code_generator", "code_reviewer")
        workflow.add_edge("code_reviewer", "test_generator")
        workflow.add_edge("test_generator", "orchestrator")

        # 添加条件边
        workflow.add_conditional_edges(
            "orchestrator",
            self._route_next_task,
            {
                "code_generator": "code_generator",
                "test_generator": "test_generator",
                END: END,
            }
        )

        return workflow.compile()

    async def _planner_node(self, state: GameDevState) -> Dict[str, Any]:
        """规划节点 - 解析需求并生成任务计划"""
        task_plan = await self.planner.plan(state)
        return {
            "task_plan": task_plan,
            "current_phase": "planning_complete",
        }

    async def _code_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码生成节点 - 根据任务生成代码"""
        current_task_id = state.get("current_task_id")
        task_plan = state.get("task_plan", [])

        # 查找当前任务
        current_task = None
        for task in task_plan:
            if task.get("id") == current_task_id:
                current_task = task
                break

        if not current_task:
            return {"current_phase": "no_task"}

        # 生成代码
        code_artifacts = await self.code_generator.generate(state, current_task)

        # 更新任务状态
        for task in task_plan:
            if task.get("id") == current_task_id:
                task["status"] = TaskStatus.COMPLETED.value
                break

        return {
            "code_generated": {
                **state.get("code_generated", {}),
                **{art["file_path"]: art["content"] for art in code_artifacts}
            },
            "code_artifacts": state.get("code_artifacts", []) + code_artifacts,
            "task_plan": task_plan,
            "current_phase": "code_generated",
        }

    async def _code_reviewer_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码审查节点 - 审查生成的代码"""
        review_result = await self.code_reviewer.review(state)
        return {"current_phase": "code_reviewed"}

    async def _test_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """测试生成节点 - 为代码生成测试用例"""
        test_code = await self.test_generator.generate(state)

        # 更新任务状态
        task_plan = state.get("task_plan", [])
        current_task_id = state.get("current_task_id")
        for task in task_plan:
            if task.get("id") == current_task_id:
                task["status"] = TaskStatus.COMPLETED.value
                break

        return {
            "code_generated": {
                **state.get("code_generated", {}),
                **test_code,
            },
            "task_plan": task_plan,
            "current_phase": "test_generated",
        }

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 - 调度下一个任务"""
        task_plan = state.get("task_plan", [])

        # 查找下一个待执行的任务
        next_task = None
        for task in task_plan:
            if task.get("status") == TaskStatus.PENDING.value:
                # 检查依赖是否满足
                dependencies = task.get("dependencies", [])
                all_deps_met = all(
                    self._is_task_completed(task_plan, dep_id)
                    for dep_id in dependencies
                )
                if all_deps_met:
                    next_task = task
                    break

        if not next_task:
            return {
                "current_phase": "workflow_complete",
                "is_complete": True,
            }

        return {
            "current_task_id": next_task.get("id"),
            "current_phase": "task_assigned",
        }

    def _route_next_task(self, state: GameDevState) -> str:
        """路由到下一个任务节点"""
        current_phase = state.get("current_phase", "")

        if current_phase == "workflow_complete":
            return END

        task_plan = state.get("task_plan", [])
        current_task_id = state.get("current_task_id")

        if not current_task_id:
            return END

        # 查找当前任务
        current_task = None
        for task in task_plan:
            if task.get("id") == current_task_id:
                current_task = task
                break

        if not current_task:
            return END

        # 根据任务类型路由
        task_type = current_task.get("type", "code")
        if task_type == "code":
            return "code_generator"
        elif task_type == "test":
            return "test_generator"
        else:
            return "code_generator"

    def _is_task_completed(self, task_plan: List[Dict], task_id: str) -> bool:
        """检查任务是否已完成"""
        for task in task_plan:
            if task.get("id") == task_id:
                return task.get("status") == TaskStatus.COMPLETED.value
        return False

    async def run(self, input_state: Dict[str, Any]) -> Dict[str, Any]:
        """运行工作流

        Args:
            input_state: 初始状态

        Returns:
            最终状态
        """
        # 初始化状态
        initial_state: GameDevState = {
            "task_plan": [],
            "current_task_id": None,
            "code_generated": {},
            "code_artifacts": [],
            "test_results": None,
            "test_report": None,
            "fix_history": [],
            "fix_attempts": 0,
            "current_phase": "initialized",
            "is_complete": False,
            "requires_human_input": False,
            "project_context": input_state.get("project_context", {}),
            "error_log": [],
        }

        # 运行工作流
        final_state = await self.workflow.ainvoke(initial_state)
        return final_state


def create_workflow(config: Dict[str, Any]) -> GameDevWorkflow:
    """创建工作流实例

    Args:
        config: 配置信息

    Returns:
        工作流实例
    """
    return GameDevWorkflow(config)
