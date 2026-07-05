"""GameForge - AI Model Adapter 接口定义

定义 ILLMClient 抽象接口、Observation（观察）和 Action（动作）数据模型。
所有 LLM 后端必须实现 ILLMClient 接口，输出必须是结构化 JSON Action。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


# ── Observation（观察）─────────────────────────────────────────────

class Observation(BaseModel):
    """Agent 的观察输入 — 一次 LLM 调用的完整上下文

    Attributes:
        messages: 对话消息列表（system + user + assistant 历史）
        agent_type: 发起调用的 Agent 类型（如 'code_generator'）
        task_id: 关联的任务 ID（可选）
        context: 额外上下文信息（state 摘要、可用工具、约束条件等）
        metadata: 任意扩展字段
    """
    messages: List[Dict[str, str]] = Field(..., description="对话消息列表")
    agent_type: str = Field(default="unknown", description="Agent 类型")
    task_id: Optional[str] = Field(None, description="关联任务 ID")
    context: Dict[str, Any] = Field(default_factory=dict, description="额外上下文")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")


# ── Action（动作）──────────────────────────────────────────────────

class ActionType(str, Enum):
    """Action 类型枚举"""
    GENERATE_CODE = "generate_code"
    REVIEW_CODE = "review_code"
    FIX_CODE = "fix_code"
    PLAN_TASK = "plan_task"
    DESIGN_GAME = "design_game"
    GENERATE_SCENE = "generate_scene"
    GENERATE_TEST = "generate_test"
    REFACTOR = "refactor"
    ANALYZE_ERROR = "analyze_error"
    CHAT = "chat"
    UNKNOWN = "unknown"


class Action(BaseModel):
    """结构化 Action — LLM 的标准输出格式

    所有 ILLMClient 实现必须返回此结构。
    内容从 LLM 原始回复中提取并规范化。

    Attributes:
        action_type: 动作类型
        content: 核心文本内容（代码 / 分析 / 计划等）
        data: 结构化数据（JSON 字段，如解析后的对象）
        reasoning: 推理过程（可选，模型的思考链）
        confidence: 置信度 0~1（可选）
        raw_response: LLM 原始完整回复
        model: 使用的模型名
        provider: 使用的 provider 名
        token_usage: token 用量统计
        created_at: 生成时间
    """
    action_type: ActionType = Field(default=ActionType.UNKNOWN, description="动作类型")
    content: str = Field(default="", description="核心文本内容")
    data: Dict[str, Any] = Field(default_factory=dict, description="结构化数据")
    reasoning: Optional[str] = Field(None, description="推理过程")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="置信度")
    raw_response: str = Field(default="", description="LLM 原始完整回复")
    model: str = Field(default="", description="使用的模型名")
    provider: str = Field(default="", description="使用的 provider 名")
    token_usage: Dict[str, int] = Field(default_factory=dict, description="token 用量")
    created_at: datetime = Field(default_factory=datetime.now, description="生成时间")

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（兼容旧接口）"""
        return self.model_dump(mode="json")


# ── ILLMClient（抽象接口）─────────────────────────────────────────

class ILLMClient(ABC):
    """LLM 客户端抽象接口

    所有后端实现（OpenAI / Mock / 本地模型等）必须继承此类。
    核心方法: generate(observation) -> Action
    """

    @abstractmethod
    async def generate(self, observation: Observation) -> Action:
        """根据 Observation 生成结构化 Action

        Args:
            observation: Agent 的观察输入

        Returns:
            Action: 结构化 JSON Action
        """
        ...

    @abstractmethod
    async def generate_stream(self, observation: Observation):
        """流式生成 — 逐步产出 Action 片段

        Args:
            observation: Agent 的观察输入

        Yields:
            Action: 逐步构建的 Action（content 逐步增长）
        """
        ...
        yield  # type: ignore[misc] — 使函数成为异步生成器

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """返回后端名称（如 'openai', 'mock'）"""
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """检查后端是否可用（API key 配置、连接状态等）"""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} backend={self.backend_name}>"
