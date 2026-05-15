"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
"""

from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

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
        workflow.add_node("test_executor", self._test_executor_node)
        workflow.add_node("debugger", self._debugger_node)
        workflow.add_node("orchestrator", self._orchestrator_node)

        # 设置入口点
        workflow.set_entry_point("planner")

        # 添加边
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("code_generator", "code_reviewer")
        workflow.add_edge("code_reviewer", "test_generator")
        workflow.add_edge("test_generator", "test_executor")
        workflow.add_edge("debugger", "code_generator")

        # 添加条件边
        workflow.add_conditional_edges(
            "orchestrator",
            self._route_next_task,
            {
                "code_generator": "code_generator",
                "test_generator": "test_generator",
                "debugger": "debugger",
                "end": END,
            }
        )

        workflow.add_conditional_edges(
            "test_executor",
            self._check_test_results,
            {
                "pass": "orchestrator",
                "fail": "debugger",
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
        task = self._find_task(state["task_plan"], current_task_id)

        if not task:
            return {"error_log": ["No task found"]}

        code_artifacts = await self.code_generator.generate(state, task)
        return {
            "code_generated": {
                **state.get("code_generated", {}),
                **{art["file_path"]: art["content"] for art in code_artifacts}
            },
            "code_artifacts": state.get("code_artifacts", []) + code_artifacts,
            "current_phase": "code_generated",
        }

    async def _code_reviewer_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码审查节点 - 审查生成的代码"""
        review_result = await self.code_reviewer.review(state)

        if not review_result["passed"]:
            return {
                "current_phase": "code_review_failed",
                "error_log": state.get("error_log", []) + review_result.get("issues", []),
            }

        return {"current_phase": "code_review_passed"}

    async def _test_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """测试生成节点 - 为代码生成测试用例"""
        test_code = await self.test_generator.generate(state)
        return {
            "code_generated": {
                **state.get("code_generated"),
                **test_code,
            },
            "current_phase": "test_generated",
        }

    async def _test_executor_node(self, state: GameDevState) -> Dict[str, Any]:
        """测试执行节点 - 执行测试并生成报告"""
        test_report = await self._execute_tests(state)
        return {
            "test_results": test_report,
            "test_report": test_report,
            "current_phase": "test_executed",
        }

    async def _debugger_node(self, state: GameDevState) -> Dict[str, Any]:
        """调试节点 - 分析错误并生成修复方案"""
        fix_result = await self.debugger.fix(state)
        return {
            "fix_history": state.get("fix_history", []) + [fix_result],
            "fix_attempts": state.get("fix_attempts", 0) + 1,
            "current_phase": "fix_applied",
        }

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 - 调度下一个任务"""
        next_task = await self.orchestrator.get_next_task(state)
        return {
            "current_task_id": next_task.get("task_id"),
            "current_phase": "task_assigned",
        }

    def _route_next_task(self, state: GameDevState) -> str:
        """路由到下一个任务节点

        Returns:
            下一个节点名称
        """
        current_phase = state.get("current_phase", "")

        if current_phase == "workflow_complete":
            return "end"

        task_plan = state.get("task_plan", [])
        current_task_id = state.get("current_task_id")

        if not current_task_id:
            return "end"

        task = self._find_task(task_plan, current_task_id)
        if not task:
            return "end"

        # 根据任务类型路由到不同Agent
        task_type = task.get("type", "code")
        if task_type == "code":
            return "code_generator"
        elif task_type == "test":
            return "test_generator"
        elif task_type == "fix":
            return "debugger"
        else:
            return "code_generator"

    def _check_test_results(self, state: GameDevState) -> str:
        """检查测试结果

        Returns:
            "pass" 或 "fail"
        """
        test_report = state.get("test_report")
        if not test_report:
            return "fail"

        success_rate = test_report.get("success_rate", 0)
        fix_attempts = state.get("fix_attempts", 0)
        max_fix_attempts = self.config.get("agents", {}).get("debugger", {}).get("max_fix_attempts", 5)

        # 如果修复次数超过最大值，需要人工介入
        if fix_attempts >= max_fix_attempts:
            return "end"

        # 根据测试通过率决定
        if success_rate >= 0.8:
            return "pass"
        else:
            return "fail"

    def _find_task(self, task_plan: List[Dict], task_id: str) -> Optional[Dict]:
        """在任务计划中查找任务"""
        for task in task_plan:
            if task.get("id") == task_id:
                return task
        return None

    async def _execute_tests(self, state: GameDevState) -> Dict[str, Any]:
        """执行测试"""
        # TODO: 实现实际的测试执行逻辑
        return {
            "total_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "success_rate": 0.0,
            "execution_time": 0.0,
            "results": [],
        }

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
