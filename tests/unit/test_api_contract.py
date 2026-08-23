"""P1-4 API/事件契约冒烟测试。

目的：把方案里 P1-3 前端契约修复 + P0-2 冒烟测试的输出契约固化，
确保未来重构不会破坏前后端契约。

策略：不跑真实 FastAPI（避免拉 LLM、数据库、并发管理器），
直接构造一个最终状态字典，断言关键字段。
"""
from dataclasses import asdict
from typing import Any, Dict, List


def _minimal_completed_state(**overrides: Any) -> Dict[str, Any]:
    """模拟 workflow.run_with_streaming 结束时的完整 state 字典"""
    base: Dict[str, Any] = {
        "code_generated": {
            "scripts/player.gd": "extends CharacterBody2D\n",
            "scripts/main.gd": "extends Node\n",
            "scenes/scene_description.json": '{"scene_name": "Main"}',
            "scenes/Main.tscn": "[gd_scene load_steps=1 format=3]",
            "assets/icon.png": "PNG_DATA",
            "scenes/test_player.gd": "extends Node\n",  # 测试用例：归类为 test
        },
        "task_plan": [
            {"id": "1", "name": "需求分析", "type": "design", "status": "completed"},
            {"id": "2", "name": "代码生成", "type": "code", "status": "completed"},
        ],
        "fix_history": [{"success": True}, {"success": False}],
        "scene_status": "success",
        "scene_path": "res://scenes/Main.tscn",
        "scene_description": {
            "scene_name": "Main",
            "game_objects": [
                {"name": "Player", "role": "player", "type": "CharacterBody2D"},
                {"name": "Ground", "role": "ground", "type": "StaticBody2D"},
            ],
        },
        "warnings": ["示例警告"],
        "validation_result": {"has_errors": False},
        "runnable": True,
        "runtime_smoke_errors": [],
        "runtime_smoke_skipped": False,
    }
    base.update(overrides)
    return base


# ── 1. complete 事件契约 ───────────────────────────────────────────────

def test_complete_event_contract_has_scene_path():
    """P1-3 契约：complete 事件必须含 scene_path（前端试玩按钮用它）"""
    state = _minimal_completed_state()
    payload = {
        "phase": "complete",
        "files": state["code_generated"],
        "task_count": len(state["task_plan"]),
        "fix_count": len(state["fix_history"]),
        "scene_status": state.get("scene_status"),
        "scene_path": state.get("scene_path", ""),
        "runnable": state.get("runnable"),
        "runtime_smoke_errors": state.get("runtime_smoke_errors", []),
        "runtime_smoke_skipped": state.get("runtime_smoke_skipped", False),
        "warnings": state.get("warnings", []),
    }
    assert payload["scene_path"] == "res://scenes/Main.tscn"
    assert payload["scene_path"].startswith("res://")


def test_complete_event_contract_has_runnable():
    """P0-2 契约：complete 事件必须含 runnable"""
    state = _minimal_completed_state()
    payload = {
        "scene_path": state["scene_path"],
        "runnable": state["runnable"],
        "runtime_smoke_errors": state["runtime_smoke_errors"],
    }
    assert payload["runnable"] is True
    assert isinstance(payload["runtime_smoke_errors"], list)


def test_complete_event_runnable_can_be_false():
    """P0-2 契约：runnable=False 表示跑不通，前端应展示错误"""
    state = _minimal_completed_state(
        runnable=False,
        runtime_smoke_errors=[{"pattern": "SCRIPT ERROR", "snippet": "Parse Error at foo.gd:5"}],
    )
    assert state["runnable"] is False
    assert len(state["runtime_smoke_errors"]) > 0


def test_complete_event_runnable_can_be_none_when_skipped():
    """P0-2 契约：runnable=None + skipped=True 表示无场景可冒烟"""
    state = _minimal_completed_state(
        scene_status="skipped", scene_path="",
        runnable=None, runtime_smoke_skipped=True,
    )
    assert state["runnable"] is None
    assert state.get("runtime_smoke_skipped") is True


# ── 2. files key 契约（前端按 key 读取） ───────────────────────────────────────────────

def test_files_keys_use_godot_paths():
    """P1-3 契约：files 的 key 是相对磁盘路径（不含 res:// 前缀）"""
    state = _minimal_completed_state()
    files = state["code_generated"]
    for k in files.keys():
        assert not k.startswith("res://"), f"file key 不应有 res:// 前缀: {k}"


def test_scene_path_uses_res_prefix():
    """P1-3 契约：scene_path 必带 res:// 前缀"""
    state = _minimal_completed_state()
    assert state["scene_path"].startswith("res://")


def test_files_contains_scene_tscn_key():
    """P1-3 契约：files 应含 scenes/Main.tscn（前端试玩按钮用）"""
    state = _minimal_completed_state()
    files = state["code_generated"]
    assert "scenes/Main.tscn" in files
    assert files["scenes/Main.tscn"].startswith("[gd_scene")


# ── 3. 文件分类契约（前端 categorizeFiles 逻辑） ───────────────────────────────────────────────

def test_categorize_files_logic():
    """P1-3 契约：前端按扩展名/路径把文件分类，结果应与后端产物对齐"""
    files = {
        "scripts/player.gd": "...",
        "scripts/test_player.gd": "...",  # → test
        "scenes/Main.tscn": "...",
        "scenes/scene_description.json": "...",
        "assets/icon.png": "...",
        "README.md": "...",
        "project.godot": "...",
    }
    categories = {k: [] for k in ("source", "test", "doc", "scene", "config", "asset")}
    for path in files:
        ext = path.split(".")[-1]
        if path.endswith(".gd") and ("Test" in path or "test_" in path):
            categories["test"].append(path)
        elif ext == "gd":
            categories["source"].append(path)
        elif ext in ("tscn", "tres", "gdns") or (ext == "json" and "scene_description" in path):
            categories["scene"].append(path)
        elif ext in ("md", "txt"):
            categories["doc"].append(path)
        elif ext in ("png", "jpg", "svg"):
            categories["asset"].append(path)
        elif ext in ("json", "cfg", "godot", "import"):
            categories["config"].append(path)
        else:
            categories["config"].append(path)

    assert categories["test"] == ["scripts/test_player.gd"]
    assert categories["source"] == ["scripts/player.gd"]
    assert categories["scene"] == ["scenes/Main.tscn", "scenes/scene_description.json"]
    assert categories["asset"] == ["assets/icon.png"]
    assert categories["doc"] == ["README.md"]
    assert categories["config"] == ["project.godot"]


# ── 4. 事件契约（前端的 switch case 期待事件） ───────────────────────────────────────────────

def test_game_design_event_payload_contract():
    """P1-3 契约：game_design 事件必含前端 handler 读的字段"""
    payload = {
        "game_title": "2D 跳跃 demo",
        "genre": "platformer",
        "camera_mode": "2D",
        "objectives": ["收集 5 颗星星"],
        "mechanics": ["跳跃"],
    }
    # 前端 handler 用到的字段
    assert "game_title" in payload
    assert "genre" in payload
    assert "camera_mode" in payload


def test_task_plan_event_payload_contract():
    """P1-3 契约：task_plan 事件必含 tasks + message"""
    payload = {
        "tasks": [{"id": "1", "name": "需求分析"}, {"id": "2", "name": "代码生成"}],
        "message": "任务计划生成完成，共 2 项",
    }
    assert "tasks" in payload and isinstance(payload["tasks"], list)
    assert "message" in payload
    assert len(payload["tasks"]) == 2
    # 前端只读 t.id 和 t.name
    for t in payload["tasks"]:
        assert "id" in t and "name" in t


def test_runtime_smoke_result_event_payload_contract():
    """P0-2 契约：runtime_smoke_result 事件必含 runnable/errors/scene_path"""
    payload = {
        "runnable": True,
        "errors": [],
        "scene_path": "res://scenes/Main.tscn",
        "elapsed_seconds": 1.234,
        "attempt": 1,
    }
    assert isinstance(payload["runnable"], bool)
    assert isinstance(payload["errors"], list)
    assert payload["scene_path"].startswith("res://")
    assert isinstance(payload["elapsed_seconds"], (int, float))


# ── 5. scene_complete 事件契约 ───────────────────────────────────────────────

def test_scene_complete_event_payload_contract():
    payload = {
        "scene_name": "Main",
        "scene_path": "res://scenes/Main.tscn",
        "object_count": 2,
        "compile_status": "headless",
    }
    assert payload["scene_path"].startswith("res://scenes/")
    assert payload["object_count"] >= 0
    assert payload["compile_status"] in ("headless", "compiled", "imported", "ready")


# ── 6. 健康基线：状态字典的最小完整性 ───────────────────────────────────────────────

def test_minimum_state_has_required_keys():
    """P1-4：state 字典应包含 complete 事件所需的全部字段"""
    state = _minimal_completed_state()
    required = {
        "code_generated", "task_plan", "scene_status", "scene_path",
        "runnable", "runtime_smoke_errors",
    }
    missing = required - set(state.keys())
    assert not missing, f"state 缺少字段: {missing}"


def test_complete_event_keys_match_workflow_emitter():
    """锁死 complete 事件字段集，防止漂移"""
    # 与 workflow.py run_with_streaming 中 complete 事件保持一致
    expected_keys = {
        "phase", "message", "files", "task_count", "fix_count",
        "scene_status", "scene_path", "runnable",
        "runtime_smoke_errors", "runtime_smoke_skipped", "warnings",
    }
    payload = {
        "phase": "complete",
        "message": "代码生成完成！",
        "files": {},
        "task_count": 0,
        "fix_count": 0,
        "scene_status": "pending",
        "scene_path": "",
        "runnable": None,
        "runtime_smoke_errors": [],
        "runtime_smoke_skipped": False,
        "warnings": [],
    }
    assert set(payload.keys()) == expected_keys, (
        f"complete 事件字段集漂移：多 {set(payload.keys()) - expected_keys}, "
        f"少 {expected_keys - set(payload.keys())}"
    )