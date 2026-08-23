"""测试 P0 组件参数化模板快路径：命中判定、参数填充、不触发 LLM。"""

import asyncio

import pytest


# ---------------- godot_templates 纯函数 ----------------

def _task(name: str, desc: str = "", task_id: str = "task_001") -> dict:
    return {"id": task_id, "name": name, "description": desc}


def test_match_component_player_task_name():
    from src.agents.code_generator.godot_templates import match_component
    assert match_component(_task("实现Player控制器")) == "player"


def test_match_component_enemy_and_coin():
    from src.agents.code_generator.godot_templates import match_component
    assert match_component(_task("创建敌人AI")) == "enemy"
    assert match_component(_task("金币收集系统")) == "coin"


def test_match_component_new_components():
    from src.agents.code_generator.godot_templates import match_component, build_artifact
    cases = {
        "实现子弹发射": "bullet",
        "布置尖刺陷阱": "hazard",
        "铺设地面地形": "ground",
        "音频音效管理": "audio",
        "关卡切换管理": "level",
        "创建移动平台": "moving_platform",
    }
    for name, kind in cases.items():
        assert match_component(_task(name)) == kind, name
        art = build_artifact(kind, {}, _task(name))
        assert art is not None, name
        assert art["metadata"]["from_template"] == kind
        assert "{{" not in art["content"], name


def test_moving_platform_prioritized_over_ground():
    """'移动平台' 任务名含 '平台'，应命中 moving_platform 而非 ground。"""
    from src.agents.code_generator.godot_templates import match_component
    assert match_component(_task("移动平台")) == "moving_platform"


def test_match_component_unknown_returns_none():
    from src.agents.code_generator.godot_templates import match_component
    assert match_component(_task("实现自定义Boss阶段")) is None


def test_render_fills_placeholders_correctly():
    from src.agents.code_generator.godot_templates import build_artifact
    art = build_artifact("player", {"physics_settings": {"move_speed": 300.0}}, _task("实现Player"))
    assert art is not None
    content = art["content"]
    assert "extends CharacterBody2D" in content
    # 占位符被替换
    assert "{{" not in content
    assert "@export var move_speed: float = 300.0" in content


def test_extract_params_uses_defaults_when_no_gdm():
    from src.agents.code_generator.godot_templates import extract_params
    params = extract_params("player", {}, _task("实现Player"))
    assert params["move_speed"] == 200.0
    assert params["player_health"] == 3


def test_extract_params_overrides_from_gdm():
    from src.agents.code_generator.godot_templates import extract_params
    gdm = {"physics_settings": {"move_speed": 250.0, "health": 5}}
    params = extract_params("player", gdm, _task("实现Player"))
    assert params["move_speed"] == 250.0
    assert params["player_health"] == 5


def test_artifact_metadata_marks_template_kind():
    from src.agents.code_generator.godot_templates import build_artifact
    art = build_artifact("coin", {}, _task("金币"))
    assert art["metadata"]["from_template"] == "coin"
    assert art["language"] == "gdscript"


# ---------------- CodeGeneratorAgent 集成 ----------------

def _make_agent_config(template_first):
    return {
        "llm": {"models": {}},
        "agents": {
            "code_generator": {
                "supported_engines": ["godot"],
                "template_first": template_first,
            }
        },
    }


def _make_state(requirements: str):
    return {
        "project_context": {
            "requirements": requirements,
            "project_name": "Test",
            "engine": "godot",
        },
        "game_design_model": {"physics_settings": {"move_speed": 300.0}},
        "code_generated": {},
        "code_artifacts": [],
        "file_metadata": {},
    }


def test_component_quick_path_skips_llm_when_enabled():
    from src.agents.code_generator import CodeGeneratorAgent
    from src.core.state.game_state import AgentType
    from src.utils.llm_client import get_llm_client

    agent = CodeGeneratorAgent(_make_agent_config(template_first=True))
    task = _task("实现Player控制器", task_id="task_001")
    state = _make_state("制作一个2D平台跳跃游戏")

    # 关闭真实的 LLM 底层，若走了 LLM 路径会抛错/阻塞
    orig_chat = agent.llm.chat
    agent.llm.chat = None  # type: ignore

    async def run():
        return await agent.generate(state, task)

    artifacts = asyncio.run(run())
    assert len(artifacts) == 1
    assert artifacts[0]["metadata"]["from_template"] == "player"
    assert "@export var move_speed: float = 300.0" in artifacts[0]["content"]
    agent.llm.chat = orig_chat


def test_disabled_template_first_falls_back_to_llm():
    from src.agents.code_generator import CodeGeneratorAgent

    agent = CodeGeneratorAgent(_make_agent_config(template_first=False))

    captured = {}

    async def fake_chat(*args, **kwargs):
        captured["called"] = True
        return "```gdscript\n# 文件: res://scripts/custom.gd\nextends Node\nfunc _ready() -> void:\n    pass\n```"

    agent.llm.chat = fake_chat  # type: ignore
    task = _task("实现Player控制器", task_id="task_001")
    state = _make_state("制作一个2D平台跳跃游戏")

    async def run():
        return await agent.generate(state, task)

    artifacts = asyncio.run(run())
    assert captured.get("called") is True, "关闭快路径后应走 LLM"
    assert artifacts, "LLM 路径应产出 artifacts"


def test_unknown_component_still_uses_llm():
    from src.agents.code_generator import CodeGeneratorAgent

    agent = CodeGeneratorAgent(_make_agent_config(template_first=True))
    captured = {}

    async def fake_chat(*args, **kwargs):
        captured["called"] = True
        return "```gdscript\n# 文件: res://scripts/boss.gd\nextends Node\nfunc _ready() -> void:\n    pass\n```"

    agent.llm.chat = fake_chat  # type: ignore
    task = _task("实现Boss血肉阶段", task_id="task_009")
    state = _make_state("制作一个含Boss的游戏")

    async def run():
        return await agent.generate(state, task)

    artifacts = asyncio.run(run())
    assert captured.get("called") is True
    assert artifacts


# ---------------- 参数填充全覆盖 ----------------

def test_all_components_param_override_from_gdm():
    """每个组件的参数都应从 GDM 提取可覆盖默认值。"""
    from src.agents.code_generator.godot_templates import build_artifact, supported_kinds

    cases = {
        # kind -> gdm -> 期望(变量,值)断言片段
        "enemy": ({"physics_settings": {"enemy_speed": 99, "patrol_distance": 88}}, "@export var move_speed: float = 99.0"),
        "coin": ({"score_value": 7}, "@export var score_value: int = 7"),
        "camera": ({"smooth_speed": 9}, "@export var smooth_speed: float = 9.0"),
        "bullet": ({"bullet_speed": 850, "bullet_damage": 3}, "@export var bullet_speed: float = 850.0"),
        "hazard": ({"hazard_damage": 4}, "@export var damage: int = 4"),
        "level": ({"total_levels": 6}, "@export var total_levels: int = 6"),
        "moving_platform": ({"travel_distance": 300, "platform_speed": 70}, "@export var travel_distance: float = 300.0"),
        "player": ({"physics_settings": {"move_speed": 220, "health": 5}}, "var _health: int = 5"),
        "game_manager": ({"total_coins": 9, "physics_settings": {"health": 4}}, "var _health: int = 4"),
    }
    for kind, (gdm, expected) in cases.items():
        art = build_artifact(kind, gdm, _task(f"实现{kind}"))
        assert art is not None, kind
        assert expected in art["content"], (kind, art["content"])


def test_unknown_kind_build_and_render_safe():
    from src.agents.code_generator.godot_templates import build_artifact, render_template
    assert build_artifact("not_a_component", {}, _task("自定义")) is None
    assert render_template("not_a_component", {}) == ""


# ---------------- 英文 / 大小写命中 ----------------

def test_english_component_names_case_insensitive():
    from src.agents.code_generator.godot_templates import match_component
    assert match_component(_task("Create PlayerController")) == "player"
    assert match_component(_task("ENEMY AI")) == "enemy"
    assert match_component(_task("HUD Manager")) == "ui"


# ---------------- 集成：config 默认行为 ----------------

def _make_agent_config_template_first_unset():
    return {
        "llm": {"models": {}},
        "agents": {"code_generator": {"supported_engines": ["godot"]}},
    }


def test_template_first_defaults_enabled_without_config():
    """未显式配置 template_first 时，默认应启用（走组件快路径，不调 LLM）。"""
    from src.agents.code_generator import CodeGeneratorAgent

    agent = CodeGeneratorAgent(_make_agent_config_template_first_unset())
    assert agent._component_template_enabled() is True

    task = _task("实现Coin收集", task_id="task_010")
    state = _make_state("金币收集玩法")

    async def run():
        return await agent.generate(state, task)

    artifacts = asyncio.run(run())
    assert len(artifacts) == 1
    assert artifacts[0]["metadata"]["from_template"] == "coin"
    assert "{{" not in artifacts[0]["content"]


def test_artifact_metadata_structure_complete():
    from src.agents.code_generator.godot_templates import build_artifact
    art = build_artifact("enemy", {}, _task("创建敌人", task_id="t007"))
    assert art is not None
    meta = art["metadata"]
    assert meta["source_task"] == "t007"
    assert meta["dependencies"] == []
    assert meta["required_components"] == []
    assert meta["from_template"] == "enemy"


def test_required_components_carried_in_metadata():
    from src.agents.code_generator.godot_templates import build_artifact
    task = {"id": "t008", "name": "实现Player", "required_components": ["CharacterBody2D", "Sprite2D"]}
    art = build_artifact("player", {}, task)
    assert art is not None
    assert art["metadata"]["required_components"] == ["CharacterBody2D", "Sprite2D"]