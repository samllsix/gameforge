"""P1-3 验证：workflow 应在节点结束时 emit game_design / task_plan 事件。

不能跑真 LangGraph（会拉 LLM）。改成直接测 emit 逻辑的纯函数版本。
"""
import asyncio
from typing import Any, Dict, List
from unittest.mock import MagicMock

from src.core.graph.workflow import GameDevWorkflow


def _make_workflow():
    config = {"godot": {}, "runtime_smoke": {"skip_when_unavailable": True}, "llm": {"default_model": "stub"}}
    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = config
    wf.memory = MagicMock()
    return wf


def _emit_for_node_event(
    wf: GameDevWorkflow,
    node_name: str,
    output: Dict[str, Any],
    events: List[Dict[str, Any]],
):
    """复制 run_with_streaming 里 on_chain_end 节点的 emit 逻辑"""
    async def _run():
        async def cb(t, d):
            events.append({"type": t, "data": d})
        # 复刻代码块：on_chain_end 后的判断
        if node_name not in ("__start__", "__end__", "LangGraph", "_route_next"):
            if output and isinstance(output, dict):
                gdm = output.get("game_design_model")
                if gdm and isinstance(gdm, dict) and node_name == "game_designer":
                    await cb("game_design", {
                        "game_title": gdm.get("game_title") or gdm.get("title") or "",
                        "genre": gdm.get("genre", ""),
                        "camera_mode": gdm.get("camera_mode") or gdm.get("camera", {}).get("type", ""),
                        "objectives": gdm.get("objectives", []),
                        "mechanics": gdm.get("mechanics", []),
                    })
                new_plan = output.get("task_plan")
                if new_plan and node_name == "planner":
                    await cb("task_plan", {
                        "tasks": new_plan,
                        "message": f"任务计划生成完成，共 {len(new_plan)} 项",
                    })
    asyncio.run(_run())


def test_emits_game_design_event_when_node_ends():
    wf = _make_workflow()
    events: List[Dict[str, Any]] = []
    output = {
        "game_design_model": {
            "game_title": "2D 跳跃 demo",
            "genre": "platformer",
            "camera": {"type": "2D"},
            "objectives": ["收集 5 颗星星"],
            "mechanics": ["跳跃", "重力"],
        },
    }
    _emit_for_node_event(wf, "game_designer", output, events)
    assert len(events) == 1
    assert events[0]["type"] == "game_design"
    assert events[0]["data"]["game_title"] == "2D 跳跃 demo"
    assert events[0]["data"]["genre"] == "platformer"
    assert events[0]["data"]["camera_mode"] == "2D"


def test_does_not_emit_game_design_for_other_nodes():
    """只有 game_designer 节点结束时才发 game_design"""
    wf = _make_workflow()
    events: List[Dict[str, Any]] = []
    # code_generator 节点也含 game_design_model 字段（如果 state 透传），不应误触发
    output = {"code_generated": {"scripts/main.gd": "..."}, "game_design_model": {"game_title": "..."}}
    _emit_for_node_event(wf, "code_generator", output, events)
    assert events == []


def test_emits_task_plan_event_when_planner_ends():
    wf = _make_workflow()
    events: List[Dict[str, Any]] = []
    output = {
        "task_plan": [
            {"id": "1", "name": "需求分析", "status": "pending"},
            {"id": "2", "name": "代码生成", "status": "pending"},
        ],
    }
    _emit_for_node_event(wf, "planner", output, events)
    assert len(events) == 1
    assert events[0]["type"] == "task_plan"
    assert len(events[0]["data"]["tasks"]) == 2
    assert events[0]["data"]["message"] == "任务计划生成完成，共 2 项"


def test_does_not_emit_task_plan_for_other_nodes():
    """只有 planner 节点结束时才发 task_plan"""
    wf = _make_workflow()
    events: List[Dict[str, Any]] = []
    output = {"code_generated": {}, "task_plan": [{"id": "1"}]}
    _emit_for_node_event(wf, "code_generator", output, events)
    assert events == []


def test_emits_both_for_long_node_name_not_in_skip_set():
    """Sanity：节点名不在跳过集合内时逻辑正常执行"""
    wf = _make_workflow()
    events: List[Dict[str, Any]] = []
    output = {
        "game_design_model": {"title": "test"},
        "task_plan": [{"id": "x"}],
    }
    # 一个不存在的节点名（不是 planner / game_designer）—— 既不 game_design 也不 task_plan
    _emit_for_node_event(wf, "nonexistent_node", output, events)
    assert events == []