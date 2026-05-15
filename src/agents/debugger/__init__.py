"""GameForge - 调试Agent模块

负责分析错误并生成修复方案。
"""

from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType


class DebuggerAgent(BaseAgent):
    """调试Agent

    负责：
    - 分析编译错误
    - 分析运行时错误
    - 分析逻辑错误
    - 生成修复方案
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化调试Agent

        Args:
            config: 配置信息
        """
        super().__init__(AgentType.DEBUGGER, config)
        self.max_fix_attempts = self.agent_config.get("max_fix_attempts", 5)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行调试任务

        Args:
            state: 当前游戏开发状态

        Returns:
            包含修复结果的状态更新
        """
        self.log_action("debugger_execute")

        fix_result = await self.fix(state)

        return {
            "fix_history": state.get("fix_history", []) + [fix_result],
            "fix_attempts": state.get("fix_attempts", 0) + 1,
            "current_phase": "fix_applied",
        }

    async def fix(self, state: GameDevState) -> Dict[str, Any]:
        """分析错误并生成修复方案

        Args:
            state: 当前游戏开发状态

        Returns:
            修复结果
        """
        self.log_action("analyze_and_fix")

        # 获取错误信息
        test_report = state.get("test_report", {})
        error_log = state.get("error_log", [])

        if not test_report and not error_log:
            return {
                "success": False,
                "error_type": "unknown",
                "error_message": "No error information available",
                "fix_description": "无法分析错误",
                "fix_code": "",
            }

        # TODO: 实现基于LLM的错误分析和修复
        # 这里先返回示例修复结果
        return {
            "success": True,
            "error_type": "NullReferenceException",
            "error_message": "Object reference not set to an instance of an object",
            "file_path": "Assets/Scripts/Player/PlayerController.cs",
            "line_number": 25,
            "fix_description": "添加空引用检查",
            "fix_code": "if (_rb == null) return;",
            "confidence": 0.9,
        }

    def analyze_error(self, error_message: str, stack_trace: str = "") -> Dict[str, Any]:
        """分析错误信息

        Args:
            error_message: 错误信息
            stack_trace: 堆栈跟踪

        Returns:
            错误分析结果
        """
        # 常见错误模式匹配
        error_patterns = {
            "NullReferenceException": {
                "type": "null_reference",
                "description": "空引用错误",
                "suggestion": "检查对象是否已初始化",
            },
            "IndexOutOfRangeException": {
                "type": "index_out_of_range",
                "description": "索引越界错误",
                "suggestion": "检查数组边界",
            },
            "InvalidCastException": {
                "type": "invalid_cast",
                "description": "类型转换错误",
                "suggestion": "检查类型兼容性",
            },
        }

        for pattern, info in error_patterns.items():
            if pattern in error_message:
                return {
                    "error_type": pattern,
                    "analysis": info,
                    "auto_fixable": True,
                }

        return {
            "error_type": "unknown",
            "analysis": {"description": "未知错误类型"},
            "auto_fixable": False,
        }
