"""GameForge - Agent基类模块

定义所有Agent的基类和通用接口。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path
import structlog

from src.core.state.game_state import GameDevState, AgentType


logger = structlog.get_logger()


class BaseAgent(ABC):
    """Agent基类

    所有GameForge Agent都继承此基类，实现统一的接口。
    """

    def __init__(self, agent_type: AgentType, config: Dict[str, Any]):
        """初始化Agent

        Args:
            agent_type: Agent类型
            config: 配置信息
        """
        self.agent_type = agent_type
        self.config = config
        self.logger = logger.bind(agent=agent_type.value)

        # 从配置中获取Agent特定配置
        self.agent_config = config.get("agents", {}).get(agent_type.value, {})

        # LLM配置 - 从 llm.models.{agent_type} 获取，回退到默认值
        llm_models = config.get("llm", {}).get("models", {})
        self.llm_config = llm_models.get(
            self._get_llm_key(),
            {"provider": "mimo", "model": "mimo-v2.5-pro", "temperature": 0.7, "max_tokens": 4096}
        )
        # 暴露 provider 和 model，供子类在调用 LLM 时使用
        self.provider = self.llm_config.get("provider")
        self.model = self.llm_config.get("model")

    def _get_llm_key(self) -> str:
        """获取LLM配置键名

        Returns:
            LLM配置键名
        """
        # 默认使用与Agent类型相同的键名
        return self.agent_type.value

    @abstractmethod
    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行Agent任务

        Args:
            state: 当前游戏开发状态
            **kwargs: 额外参数

        Returns:
            执行结果
        """
        pass

    def log_action(self, action: str, details: Optional[Dict] = None):
        """记录Agent操作

        Args:
            action: 操作名称
            details: 操作详情
        """
        self.logger.info(
            "agent_action",
            action=action,
            timestamp=datetime.now().isoformat(),
            **(details or {})
        )

    def log_error(self, error: str, details: Optional[Dict] = None):
        """记录错误

        Args:
            error: 错误信息
            details: 错误详情
        """
        # 避免 details 中的 'error' 键与参数冲突
        safe_details = {(k if k != "error" else "detail_error"): v for k, v in (details or {}).items()}
        self.logger.error(
            "agent_error",
            error_message=error,
            timestamp=datetime.now().isoformat(),
            **safe_details
        )

    def get_prompt_template(self, template_name: str) -> str:
        """获取Prompt模板

        Args:
            template_name: 模板名称

        Returns:
            模板内容
        """
        project_root = Path(__file__).resolve().parents[2]
        template_path = project_root / "config" / "prompts" / f"{template_name}.txt"

        if template_path.exists():
            with template_path.open("r", encoding="utf-8") as f:
                return f.read()

        self.logger.warning("prompt_template_not_found", template=template_name)
        return ""

    def format_state_summary(self, state: GameDevState) -> str:
        """格式化状态摘要

        Args:
            state: 游戏开发状态

        Returns:
            格式化的状态摘要
        """
        summary_parts = []

        # 任务计划
        task_plan = state.get("task_plan", [])
        if task_plan:
            summary_parts.append(f"任务数量: {len(task_plan)}")

        # 当前任务
        current_task_id = state.get("current_task_id")
        if current_task_id:
            summary_parts.append(f"当前任务: {current_task_id}")

        # 代码生成状态
        code_generated = state.get("code_generated", {})
        if code_generated:
            summary_parts.append(f"已生成文件: {len(code_generated)}")

        # 测试结果
        test_report = state.get("test_report")
        if test_report:
            success_rate = test_report.get("success_rate", 0)
            summary_parts.append(f"测试通过率: {success_rate:.1%}")

        # 修复历史
        fix_history = state.get("fix_history", [])
        if fix_history:
            summary_parts.append(f"修复次数: {len(fix_history)}")

        return " | ".join(summary_parts) if summary_parts else "无状态信息"

    def validate_state(self, state: GameDevState, required_keys: List[str]) -> bool:
        """验证状态是否包含必需的键

        Args:
            state: 游戏开发状态
            required_keys: 必需的键列表

        Returns:
            是否验证通过
        """
        for key in required_keys:
            if key not in state:
                self.logger.error("missing_state_key", key=key)
                return False
        return True

    def update_state(self, state: GameDevState, updates: Dict[str, Any]) -> GameDevState:
        """更新状态

        Args:
            state: 当前状态
            updates: 更新内容

        Returns:
            更新后的状态
        """
        return {**state, **updates}
