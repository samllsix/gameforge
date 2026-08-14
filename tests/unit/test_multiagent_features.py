"""多智能体改造第二~五步功能测试（无需 LLM / 无需 Godot）。

覆盖：Reflector 反思回环、消息总线、Debugger 委派查知识库、Tester 引擎反馈降级。
"""

import pytest

from src.agents.reflector import ReflectorAgent
from src.core.state.bus import publish, messages_for, latest
from src.core.state.game_state import GameDevState, AgentType
from src.core.knowledge.lookup import lookup_godot_knowledge


@pytest.fixture
def config():
    return {"agents": {"reflector": {"fast_reflect": True}}}


# ---------- 第二步：Reflector ----------

@pytest.mark.asyncio
async def test_reflector_ok_when_clean(config):
    agent = ReflectorAgent(config)
    state = {"error_log": [], "warnings": [], "main_review_result": {}, "design_review_result": {}}
    res = await agent.execute(state)
    assert res["verdict"] == "ok"
    assert res["replan_needed"] is False
    assert res["reflection_mode"] == "fast"


@pytest.mark.asyncio
async def test_reflector_replan_when_errors(config):
    agent = ReflectorAgent(config)
    state = {"error_log": ["player.gd:12: error: Identifier not found"], "warnings": [], "main_review_result": {}, "design_review_result": {}}
    res = await agent.execute(state)
    assert res["verdict"] == "replan"
    assert res["replan_needed"] is True


# ---------- 第三步：消息总线 ----------

def test_bus_publish_consume():
    base = {"message_bus": []}
    pub = publish("replan", sender="main_reviewer", content="设计有坑", recipient="planner")
    base["message_bus"] = base["message_bus"] + pub["message_bus"]
    inbox = messages_for(base, topic="replan", recipient="planner")
    assert len(inbox) == 1
    assert inbox[0]["from"] == "main_reviewer"
    assert latest(base, recipient="planner")["content"] == "设计有坑"


# ---------- 第四步：Debugger 委派查知识库 ----------

def test_debugger_delegate_finds_knowledge(config):
    from src.agents.debugger import DebuggerAgent

    agent = DebuggerAgent(config)
    res = agent.delegate_to_research("Parser Error: Expected colon in godot script at line 12")
    assert res["delegated"] is True
    assert len(res["findings"]) > 0
    assert "title" in res["findings"][0]


def test_lookup_godot_knowledge_offline():
    hits = lookup_godot_knowledge("move_and_slide velocity godot 4")
    assert isinstance(hits, list)
    assert len(hits) >= 1


# ---------- 第五步：Tester 引擎反馈降级 ----------

@pytest.mark.asyncio
async def test_tester_engine_feedback_degraded_when_godot_down(config):
    from src.agents.test_generator import TestGeneratorAgent

    # 端口 8765 未运行时应优雅降级，不抛异常
    agent = TestGeneratorAgent(config)
    res = await agent.read_engine_feedback()
    assert res["degraded"] is True
    assert res["engine_available"] is False
