"""GameForge - 代码审查Agent模块

负责审查生成的代码质量。
"""

from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType


class CodeReviewerAgent(BaseAgent):
    """代码审查Agent

    负责：
    - 代码规范检查
    - 设计模式检查
    - 性能分析
    - 安全性检查
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化代码审查Agent

        Args:
            config: 配置信息
        """
        super().__init__(AgentType.CODE_REVIEWER, config)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行代码审查任务

        Args:
            state: 当前游戏开发状态

        Returns:
            审查结果
        """
        self.log_action("code_reviewer_execute")

        review_result = await self.review(state)

        return {
            "current_phase": "code_review_passed" if review_result["passed"] else "code_review_failed",
            "error_log": state.get("error_log", []) + review_result.get("issues", []),
        }

    async def review(self, state: GameDevState) -> Dict[str, Any]:
        """审查代码

        Args:
            state: 当前游戏开发状态

        Returns:
            审查结果
        """
        self.log_action("review_code")

        code_generated = state.get("code_generated", {})
        if not code_generated:
            return {
                "passed": False,
                "score": 0,
                "issues": ["No code to review"],
            }

        # TODO: 实现基于LLM的代码审查
        # 这里先返回示例审查结果
        return {
            "passed": True,
            "score": 85,
            "issues": [],
            "suggestions": [
                "考虑添加更多注释",
                "可以优化某些性能瓶颈",
            ],
        }
