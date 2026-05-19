"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
"""

import asyncio
from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, END

from src.core.state.game_state import GameDevState, TaskStatus, TaskType, AgentType
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

        # 添加节点（已移除refactor节点，代码质量由code_generator直接保证）
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("code_generator", self._code_generator_node)
        workflow.add_node("code_reviewer", self._code_reviewer_node)
        workflow.add_node("test_generator", self._test_generator_node)
        workflow.add_node("orchestrator", self._orchestrator_node)
        workflow.add_node("debugger", self._debugger_node)

        # 设置入口点
        workflow.set_entry_point("planner")

        # 添加边（reviewer之后直接回到orchestrator，不再经过refactor）
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("code_generator", "code_reviewer")
        workflow.add_edge("code_reviewer", "orchestrator")
        workflow.add_edge("test_generator", "orchestrator")
        workflow.add_edge("debugger", "code_generator")  # 修复后重新生成

        # 添加条件边
        workflow.add_conditional_edges(
            "orchestrator",
            self._route_next_task,
            {
                "code_generator": "code_generator",
                "test_generator": "test_generator",
                "debugger": "debugger",
                END: END,
            }
        )

        return workflow.compile()

    async def _planner_node(self, state: GameDevState) -> Dict[str, Any]:
        """规划节点 - 解析需求并生成任务计划"""
        try:
            task_plan = await self.planner.plan(state)
            return {
                "task_plan": task_plan,
                "current_phase": "planning_complete",
            }
        except Exception as e:
            return {"error_log": [f"Planner failed: {e}"], "current_phase": "error"}

    async def _code_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码生成节点 - 根据任务生成代码"""
        try:
            current_task_id = state.get("current_task_id")
            task_plan = state.get("task_plan", [])

            current_task = None
            for task in task_plan:
                if task.get("id") == current_task_id:
                    current_task = task
                    break

            if not current_task:
                return {"current_phase": "no_task"}

            code_artifacts = await self.code_generator.generate(state, current_task)

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
        except Exception as e:
            return {"error_log": [f"Code generator failed: {e}"], "current_phase": "error"}

    async def _code_reviewer_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码审查节点 - 审查生成的代码"""
        try:
            review_result = await self.code_reviewer.review(state)
            return {"current_phase": "code_reviewed"}
        except Exception as e:
            return {"error_log": [f"Code reviewer failed: {e}"], "current_phase": "error"}

    async def _test_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """测试生成节点 - 为代码生成测试用例"""
        try:
            test_code = await self.test_generator.generate(state)
            task_plan = state.get("task_plan", [])
            current_task_id = state.get("current_task_id")
            for task in task_plan:
                if task.get("id") == current_task_id:
                    task["status"] = TaskStatus.COMPLETED.value
                    break
            return {
                "code_generated": {**state.get("code_generated", {}), **test_code},
                "task_plan": task_plan,
                "current_phase": "test_generated",
            }
        except Exception as e:
            return {"error_log": [f"Test generator failed: {e}"], "current_phase": "error"}

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 - 调度下一个任务（支持并行）"""
        try:
            task_plan = state.get("task_plan", [])
            fix_attempts = state.get("fix_attempts", 0)

            # 如果有错误日志，增加修复计数并路由到 debugger
            error_log = state.get("error_log", [])
            if error_log and fix_attempts < self.config.get("agents", {}).get("debugger", {}).get("max_fix_attempts", 5):
                return {
                    "current_phase": "needs_fix",
                    "fix_attempts": fix_attempts + 1,
                }

            ready_tasks = self._get_all_ready_tasks(task_plan)
            if not ready_tasks:
                return {"current_phase": "workflow_complete", "is_complete": True}

            ready_ids = [t.get("id") for t in ready_tasks]
            return {
                "current_task_id": ready_tasks[0].get("id"),
                "ready_task_ids": ready_ids,
                "current_phase": "task_assigned",
            }
        except Exception as e:
            return {"error_log": [f"Orchestrator failed: {e}"], "current_phase": "error"}

    async def _debugger_node(self, state: GameDevState) -> Dict[str, Any]:
        """调试节点 - 分析错误并生成修复"""
        try:
            error_log = state.get("error_log", [])
            fix_result = await self.debugger.analyze_and_fix(state, error_log)
            return {
                **fix_result,
                "current_phase": "fix_applied",
                "error_log": [],  # 清空错误日志
            }
        except Exception as e:
            return {"error_log": [f"Debugger failed: {e}"], "current_phase": "unrecoverable"}

    def _route_next_task(self, state: GameDevState) -> str:
        """路由到下一个任务节点"""
        current_phase = state.get("current_phase", "")

        if current_phase == "workflow_complete":
            return END
        if current_phase in ("error", "needs_fix"):
            return "debugger"

        task_plan = state.get("task_plan", [])
        current_task_id = state.get("current_task_id")

        if not current_task_id:
            return END

        current_task = None
        for task in task_plan:
            if task.get("id") == current_task_id:
                current_task = task
                break

        if not current_task:
            return END

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

    def _get_all_ready_tasks(self, task_plan: List[Dict]) -> List[Dict]:
        """获取所有依赖已满足的待执行任务"""
        ready = []
        for task in task_plan:
            if task.get("status") != TaskStatus.PENDING.value:
                continue
            dependencies = task.get("dependencies", [])
            all_deps_met = all(
                self._is_task_completed(task_plan, dep_id)
                for dep_id in dependencies
            )
            if all_deps_met:
                ready.append(task)
        return ready

    async def _execute_tasks_parallel(self, state: GameDevState, tasks: List[Dict]) -> Dict[str, Any]:
        """并行执行多个独立任务"""
        async def _process_single_task(task):
            task_type = task.get("type", "code")
            if task_type == TaskType.TEST.value:
                return await self.test_generator.generate(state), task, "test"
            else:
                return await self.code_generator.generate(state, task), task, "code"

        results = await asyncio.gather(
            *[_process_single_task(t) for t in tasks],
            return_exceptions=True,
        )

        merged_code = dict(state.get("code_generated", {}))
        merged_artifacts = list(state.get("code_artifacts", []))
        updated_plan = [dict(t) for t in state.get("task_plan", [])]

        errors = []
        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue

            artifacts, task, _ = result
            if isinstance(artifacts, list):
                for art in artifacts:
                    merged_code[art["file_path"]] = art["content"]
                    merged_artifacts.append(art)
            elif isinstance(artifacts, dict):
                merged_code.update(artifacts)

            for t in updated_plan:
                if t.get("id") == task.get("id"):
                    t["status"] = TaskStatus.COMPLETED.value
                    break

        return {
            "code_generated": merged_code,
            "code_artifacts": merged_artifacts,
            "task_plan": updated_plan,
            "current_phase": "code_generated",
            "error_log": errors if errors else [],
        }

    async def _run_with_parallel_support(self, state: GameDevState) -> GameDevState:
        """运行工作流（支持并行任务执行）"""
        # Phase 1: 规划
        plan_result = await self._planner_node(state)
        state.update(plan_result)

        max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)

        for _ in range(max_iterations):
            # 编排器决定下一步
            orch_result = await self._orchestrator_node(state)
            state.update(orch_result)

            if state.get("is_complete") or state.get("current_phase") == "workflow_complete":
                break

            if state.get("current_phase") in ("error", "needs_fix"):
                debug_result = await self._debugger_node(state)
                state.update(debug_result)
                continue

            ready_ids = state.get("ready_task_ids", [])
            task_plan = state.get("task_plan", [])
            ready_tasks = [t for t in task_plan if t.get("id") in ready_ids]

            if len(ready_tasks) > 1:
                # 多个独立任务 → 并行执行
                parallel_result = await self._execute_tasks_parallel(state, ready_tasks)
                state.update(parallel_result)

                # 并行执行审查和测试生成
                review_result, test_result = await asyncio.gather(
                    self._code_reviewer_node(state),
                    self._test_generator_node(state),
                )
                state.update(review_result)
                state.update(test_result)
            elif ready_tasks:
                # 单个任务 → 完整流水线（reviewer + test_generator并行）
                task = ready_tasks[0]
                state["current_task_id"] = task.get("id")
                task_type = task.get("type", TaskType.CODE.value)

                if task_type == TaskType.TEST.value:
                    # 测试任务 → 直接生成测试
                    test_result = await self._test_generator_node(state)
                    state.update(test_result)
                else:
                    # 代码任务 → 生成 + 审查 + 测试（并行）
                    gen_result = await self._code_generator_node(state)
                    state.update(gen_result)

                    # 如果代码生成失败（如不支持的任务类型），标记任务完成避免死循环
                    error_log = state.get("error_log", [])
                    if error_log:
                        task_plan = state.get("task_plan", [])
                        for t in task_plan:
                            if t.get("id") == task.get("id"):
                                t["status"] = TaskStatus.COMPLETED.value
                                break
                        state["task_plan"] = task_plan
                        state["error_log"] = []
                        continue

                    # 并行执行代码审查和测试生成
                    review_result, test_result = await asyncio.gather(
                        self._code_reviewer_node(state),
                        self._test_generator_node(state),
                    )
                    state.update(review_result)
                    state.update(test_result)
            else:
                break

        return state

    async def run(self, input_state: Dict[str, Any]) -> Dict[str, Any]:
        """运行工作流

        Args:
            input_state: 初始状态

        Returns:
            最终状态
        """
        initial_state: GameDevState = {
            "task_plan": [],
            "current_task_id": None,
            "ready_task_ids": None,
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

        final_state = await self._run_with_parallel_support(initial_state)
        return final_state

    async def run_with_streaming(
        self, input_state: Dict[str, Any], event_callback
    ) -> Dict[str, Any]:
        """运行工作流（带SSE事件回调）

        Args:
            input_state: 初始状态
            event_callback: 异步回调函数 async def callback(event_type: str, data: dict)

        Returns:
            最终状态
        """
        initial_state: GameDevState = {
            "task_plan": [],
            "current_task_id": None,
            "ready_task_ids": None,
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

        state = initial_state

        try:
            # Phase 1: 规划
            await event_callback("phase_start", {"phase": "planning", "message": "正在分析需求并生成任务计划..."})
            plan_result = await self._planner_node(state)
            state.update(plan_result)
            await event_callback("task_plan", {
                "phase": "planning_complete",
                "tasks": [
                    {"id": t.get("id"), "name": t.get("name"), "description": t.get("description")}
                    for t in state.get("task_plan", [])
                ],
                "message": f"任务计划生成完成，共{len(state.get('task_plan', []))}个任务",
            })

            max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)

            for iteration in range(max_iterations):
                orch_result = await self._orchestrator_node(state)
                state.update(orch_result)

                if state.get("is_complete") or state.get("current_phase") == "workflow_complete":
                    break

                if state.get("current_phase") in ("error", "needs_fix"):
                    await event_callback("phase_start", {"phase": "debugging", "message": "检测到错误，正在自动修复..."})
                    debug_result = await self._debugger_node(state)
                    state.update(debug_result)
                    continue

                ready_ids = state.get("ready_task_ids", [])
                task_plan = state.get("task_plan", [])
                ready_tasks = [t for t in task_plan if t.get("id") in ready_ids]

                if len(ready_tasks) > 1:
                    # 多个独立任务 → 并行执行
                    await event_callback("phase_start", {
                        "phase": "generating",
                        "message": f"正在并行生成{len(ready_tasks)}个任务...",
                    })
                    # 记录已发送的文件
                    sent_files = set(state.get("code_generated", {}).keys())
                    parallel_result = await self._execute_tasks_parallel(state, ready_tasks)
                    state.update(parallel_result)

                    # 只发送新生成的文件
                    for file_path, content in state.get("code_generated", {}).items():
                        if file_path not in sent_files:
                            await event_callback("code_file", {
                                "file_path": file_path,
                                "content": content,
                            })

                    # 并行执行审查和测试生成
                    review_result, test_result = await asyncio.gather(
                        self._code_reviewer_node(state),
                        self._test_generator_node(state),
                    )
                    state.update(review_result)
                    state.update(test_result)

                    # 发送测试生成的文件
                    for file_path, content in state.get("code_generated", {}).items():
                        if file_path not in sent_files:
                            await event_callback("code_file", {
                                "file_path": file_path,
                                "content": content,
                            })

                elif ready_tasks:
                    task = ready_tasks[0]
                    state["current_task_id"] = task.get("id")
                    task_type = task.get("type", TaskType.CODE.value)
                    await event_callback("phase_start", {
                        "phase": "generating",
                        "message": f"正在生成: {task.get('name', '')}...",
                    })

                    sent_files = set(state.get("code_generated", {}).keys())

                    if task_type == TaskType.TEST.value:
                        # 测试任务 → 直接生成测试
                        test_result = await self._test_generator_node(state)
                        state.update(test_result)
                    else:
                        # 代码任务 → 生成 + 审查 + 测试（并行）
                        gen_result = await self._code_generator_node(state)
                        state.update(gen_result)

                        # 如果代码生成失败，标记任务完成避免死循环
                        error_log = state.get("error_log", [])
                        if error_log:
                            task_plan = state.get("task_plan", [])
                            for t in task_plan:
                                if t.get("id") == task.get("id"):
                                    t["status"] = TaskStatus.COMPLETED.value
                                    break
                            state["task_plan"] = task_plan
                            state["error_log"] = []
                            continue

                        # 并行执行审查和测试
                        review_result, test_result = await asyncio.gather(
                            self._code_reviewer_node(state),
                            self._test_generator_node(state),
                        )
                        state.update(review_result)
                        state.update(test_result)

                    # 只发送新生成的文件
                    for file_path, content in state.get("code_generated", {}).items():
                        if file_path not in sent_files:
                            await event_callback("code_file", {
                                "file_path": file_path,
                                "content": content,
                            })
                else:
                    break

            await event_callback("complete", {
                "phase": "complete",
                "message": "代码生成完成！",
                "files": state.get("code_generated", {}),
                "task_count": len(state.get("task_plan", [])),
                "fix_count": len(state.get("fix_history", [])),
            })

        except Exception as e:
            await event_callback("error", {"message": f"生成过程出错: {str(e)}"})

        return state


def create_workflow(config: Dict[str, Any]) -> GameDevWorkflow:
    """创建工作流实例

    Args:
        config: 配置信息

    Returns:
        工作流实例
    """
    return GameDevWorkflow(config)
