"""GameForge - 多智能体对话会话骨架

实现 ChatDev 式的「指导者-助手」双 agent 对话 dyad（对话环）。

设计原则（与现有架构解耦）：
- DialogueSession **不直接耦合任何 LLM 调用**，只负责「轮次交替 + 终止判定 + 护栏」。
- 具体说什么由调用方以异步回调 ``guide_turn(history)`` / ``assistant_turn(history)`` 注入，
  回调返回 ``DialogueTurn(content, should_terminate, metadata)``。
- 这样 session 既可以被真实 LLM agent 驱动，也可以在测试中用假回调驱动，无需联网。

协作可见性：
- 全程对话历史落盘为 ``DialogueTranscript``，可被工作流写进 state 供审计 / 流式展示，
  让「agent 在争论」这件事变得可观测（这正是把流水线救活的第一步）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger()

# 终止符（与 ChatDev 的 <TERMINATE> 约定一致）
TERMINATE = "<TERMINATE>"


@dataclass
class DialogueMessage:
    """对话中的一条发言"""

    speaker: str          # agent 名（如 code_reviewer / refactor）
    role: str             # "guide" | "assistant"
    content: str          # 自然语言内容（可见的协作证据）
    round: int            # 第几轮
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "speaker": self.speaker,
            "role": self.role,
            "content": self.content,
            "round": self.round,
            "metadata": self.metadata,
        }


@dataclass
class DialogueTurn:
    """一次 turn 回调的返回：说什么 + 是否请求终止 + 附带结构化数据"""

    content: str
    should_terminate: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DialogueTranscript:
    """一整段对话的产物，可序列化进 state"""

    topic: str
    messages: List[DialogueMessage]
    rounds: int
    reached_consensus: bool
    terminated_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "rounds": self.rounds,
            "reached_consensus": self.reached_consensus,
            "terminated_by": self.terminated_by,
            "messages": [m.to_dict() for m in self.messages],
        }


# turn 回调类型：接收对话历史，返回一次发言
TurnFn = Callable[[List[DialogueMessage]], Awaitable[DialogueTurn]]


class DialogueSession:
    """双 agent 多轮对话会话（指导者-助手 dyad）。

    用法::

        session = DialogueSession(topic="review_refactor", max_rounds=3,
                                  guide_name="code_reviewer", assistant_name="refactor")
        transcript = await session.run(guide_turn, assistant_turn)

    每轮：指导者先发言 → 若请求终止则共识达成、结束；否则助手发言 →
    若请求终止则结束。达到 ``max_rounds`` 仍无终止则强制收敛（护栏）。
    """

    def __init__(
        self,
        topic: str,
        max_rounds: int = 3,
        guide_name: str = "guide",
        assistant_name: str = "assistant",
    ):
        self.topic = topic
        self.max_rounds = max(1, int(max_rounds))
        self.guide_name = guide_name
        self.assistant_name = assistant_name
        self.history: List[DialogueMessage] = []
        self.round = 0

    async def run(self, guide_turn: TurnFn, assistant_turn: TurnFn) -> DialogueTranscript:
        """运行对话直到共识 / 某方终止 / 达到 max_rounds。"""
        reached_consensus = False
        terminated_by: Optional[str] = None

        for r in range(1, self.max_rounds + 1):
            self.round = r

            # 1) 指导者发言
            g_turn = await guide_turn(self.history)
            self._append(self.guide_name, "guide", g_turn, r)
            if g_turn.should_terminate:
                reached_consensus = True
                terminated_by = self.guide_name
                logger.info("dialogue_terminated", by=self.guide_name, round=r, topic=self.topic)
                break

            # 2) 助手发言
            a_turn = await assistant_turn(self.history)
            self._append(self.assistant_name, "assistant", a_turn, r)
            if a_turn.should_terminate:
                reached_consensus = True
                terminated_by = self.assistant_name
                logger.info("dialogue_terminated", by=self.assistant_name, round=r, topic=self.topic)
                break

        if not reached_consensus:
            logger.warning(
                "dialogue_max_rounds_reached",
                rounds=self.round,
                topic=self.topic,
                note="已强制收敛，避免无限协商",
            )

        return DialogueTranscript(
            topic=self.topic,
            messages=self.history,
            rounds=self.round,
            reached_consensus=reached_consensus,
            terminated_by=terminated_by,
        )

    def _append(self, speaker: str, role: str, turn: DialogueTurn, round_no: int) -> None:
        self.history.append(
            DialogueMessage(
                speaker=speaker,
                role=role,
                content=turn.content,
                round=round_no,
                metadata=turn.metadata,
            )
        )
