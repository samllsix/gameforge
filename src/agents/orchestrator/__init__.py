"""GameForge - 编排Agent模块

负责任务调度和流程控制。
"""

from typing import Any, Dict, List, Optional
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType, TaskStatus


class OrchestratorAgent(BaseAgent):
    """编排Agent

    负责：
    - 任务调度
    - 流程控制
    - 状态监控
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化编排Agent

        Args:
            config: 配置信息
        """
        super().__init__(AgentType.ORCHESTRATOR, config)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行编排任务

        Args:
            state: 当前游戏开发状态

        Returns:
            更新后的状态
        """
        self.log_action("orchestrator_execute")

        # 获取下一个待执行任务
        next_task = await self.get_next_task(state)

        if not next_task:
            return {
                "current_phase": "workflow_complete",
                "is_complete": True,
            }

        return {
            "current_task_id": next_task.get("task_id"),
            "current_phase": "task_assigned",
        }

    async def get_next_task(self, state: GameDevState) -> Optional[Dict[str, Any]]:
        """获取下一个待执行任务

        Args:
            state: 当前游戏开发状态

        Returns:
            下一个任务信息，如果没有则返回None
        """
        task_plan = state.get("task_plan", [])
        if not task_plan:
            return None

        # 查找第一个待执行的任务
        for task in task_plan:
            if task.get("status") == TaskStatus.PENDING.value:
                # 检查依赖是否满足
                dependencies = task.get("dependencies", [])
                all_deps_met = all(
                    self._is_task_completed(state, dep_id)
                    for dep_id in dependencies
                )

                if all_deps_met:
                    return {
                        "task_id": task.get("id"),
                        "task_type": task.get("type"),
                        "assigned_agent": task.get("assigned_agent"),
                    }

        return None

    def _is_task_completed(self, state: GameDevState, task_id: str) -> bool:
        """检查任务是否已完成

        Args:
            state: 当前状态
            task_id: 任务ID

        Returns:
            任务是否已完成
        """
        task_plan = state.get("task_plan", [])
        for task in task_plan:
            if task.get("id") == task_id:
                return task.get("status") == TaskStatus.COMPLETED.value
        return False

    def get_workflow_progress(self, state: GameDevState) -> Dict[str, Any]:
        """获取工作流进度

        Args:
            state: 当前状态

        Returns:
            进度信息
        """
        task_plan = state.get("task_plan", [])
        total_tasks = len(task_plan)

        if total_tasks == 0:
            return {
                "total": 0,
                "completed": 0,
                "in_progress": 0,
                "pending": 0,
                "progress": 0.0,
            }

        completed = sum(
            1 for t in task_plan
            if t.get("status") == TaskStatus.COMPLETED.value
        )
        in_progress = sum(
            1 for t in task_plan
            if t.get("status") == TaskStatus.IN_PROGRESS.value
        )
        pending = sum(
            1 for t in task_plan
            if t.get("status") == TaskStatus.PENDING.value
        )

        return {
            "total": total_tasks,
            "completed": completed,
            "in_progress": in_progress,
            "pending": pending,
            "progress": completed / total_tasks if total_tasks > 0 else 0.0,
        }
