"""多智能体功能测试（无需 LLM / 无需 Godot）。

覆盖：消息总线、知识库离线检索。
"""
import pytest

from src.core.state.bus import publish, messages_for, latest
from src.core.knowledge.lookup import lookup_godot_knowledge


@pytest.fixture
def config():
    return {}


# ---------- 消息总线 ----------

def test_bus_publish_consume():
    base = {"message_bus": []}
    pub = publish("replan", sender="orchestrator", content="建议重排任务", recipient="planner")
    base["message_bus"] = base["message_bus"] + pub["message_bus"]
    inbox = messages_for(base, topic="replan", recipient="planner")
    assert len(inbox) == 1
    assert inbox[0]["from"] == "orchestrator"
    assert latest(base, recipient="planner")["content"] == "建议重排任务"


def test_lookup_godot_knowledge_offline():
    hits = lookup_godot_knowledge("move_and_slide velocity godot 4")
    assert isinstance(hits, list)
    assert len(hits) >= 1
