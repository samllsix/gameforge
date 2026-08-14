"""GameForge - 多智能体对话包

把「固定流水线」救活的第一步：让 agent 之间用自然语言多轮协商，
而非只通过共享状态单向交接。
"""

from .session import (
    DialogueSession,
    DialogueMessage,
    DialogueTurn,
    DialogueTranscript,
    TERMINATE,
)

__all__ = [
    "DialogueSession",
    "DialogueMessage",
    "DialogueTurn",
    "DialogueTranscript",
    "TERMINATE",
]
