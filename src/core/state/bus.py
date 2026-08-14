"""GameForge - 消息总线（多智能体改造第三步）

把「线性黑板」升级为「发布-订阅总线」：任意 agent 可给任意 agent 发消息，
不必按固定顺序交接。用于解耦硬编码边（如 tester 直接 @debugger、
main_reviewer 直接 @planner 等）。

使用方式：
    # agent 发消息（返回 dict，交给 LangGraph reducer 合并进 state）
    return publish("replan", sender="main_reviewer", content="设计有坑", recipient="planner")
    # 读取某 topic 的消息
    msgs = messages_for(state, topic="replan", recipient="debugger")
"""

from typing import Any, Dict, List, Optional

import time


def publish(
    topic: str,
    sender: str,
    content: Any,
    recipient: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一条总线消息，返回可被 LangGraph reducer 合并的 dict。"""
    msg = {
        "topic": topic,
        "from": sender,
        "to": recipient,
        "content": content,
        "meta": meta or {},
        "ts": time.time(),
    }
    return {"message_bus": [msg]}


def messages_for(
    state: Dict[str, Any],
    topic: Optional[str] = None,
    recipient: Optional[str] = None,
    sender: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """读取总线中匹配条件的消息（只读，不改变 state）。"""
    bus: List[Dict[str, Any]] = state.get("message_bus", []) or []
    result = []
    for m in bus:
        if topic is not None and m.get("topic") != topic:
            continue
        if recipient is not None and m.get("to") not in (recipient, None):
            continue
        if sender is not None and m.get("from") != sender:
            continue
        result.append(m)
    return result


def latest(
    state: Dict[str, Any],
    topic: Optional[str] = None,
    recipient: Optional[str] = None,
    sender: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """返回匹配条件的最新一条消息。"""
    msgs = messages_for(state, topic=topic, recipient=recipient, sender=sender)
    return msgs[-1] if msgs else None
