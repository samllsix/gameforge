"""游戏概念组合引擎测试：100 备用库 / 确定性 / 融合质量 / API 契约 / planner 接线。"""
from src.agents.genre_fusion import (
    THEME_PACKS,
    TWIST_CARDS,
    build_concept_library,
    build_concept_prompt_hint,
    fuse_genre,
    roll_concept,
)
from src.agents.genre_specs import GENRE_SPECS


def test_combination_space_and_library():
    assert len(GENRE_SPECS) == 20
    assert len(TWIST_CARDS) >= 28
    assert len(THEME_PACKS) >= 25
    # 组合空间远超 100（20 基款 × 28 变体 × 25 主题）
    assert len(GENRE_SPECS) * len(TWIST_CARDS) * len(THEME_PACKS) >= 10000

    library = build_concept_library(100)
    assert len(library) == 100
    keys = [(c.primary_id, c.secondary_id, c.twist["id"], c.theme_pack["id"]) for c in library]
    assert len(set(keys)) == 100, "备用库必须两两不重复"


def test_library_deterministic_across_calls():
    a = [c.pitch for c in build_concept_library(100, seed=42)]
    b = [c.pitch for c in build_concept_library(100, seed=42)]
    assert a == b


def test_fuse_spec_merges_both_parents():
    spec = fuse_genre("platformer", "tower_defense", twist_id="boss_rush")
    # 主基款机制 + 副基款机制 + 变体牌机制
    assert any("Boss" in m for m in spec.mechanics)
    assert len(spec.mechanics) >= 5
    # 蓝图主体来自主基款，并带入一个副基款签名角色
    roles = {e["role"] for e in spec.entities}
    assert "player" in roles
    assert spec.camera == "2d_side_view"  # 镜头跟随主基款
    assert "tower_defense" in spec.id


def test_roll_concept_reproducible():
    a, b = roll_concept(123), roll_concept(123)
    assert a.pitch == b.pitch and a.spec.id == b.spec.id
    c = roll_concept(124)
    assert c.pitch != a.pitch or c.theme_pack["id"] != a.theme_pack["id"]


def test_hint_contains_concept_and_baseline():
    lib = build_concept_library(10)
    hint = build_concept_prompt_hint(lib[0], difficulty="medium")
    assert "[游戏概念]" in hint and lib[0].pitch in hint
    assert "基本功能基线" in hint
    assert lib[0].theme_pack["name_zh"] in hint


def test_all_library_concepts_have_valid_blueprints():
    """100 个备用概念全部可以生成合法场景（实体不重名、角色合法）"""
    from src.engine.godot.scene_to_godot import default_scene_ir

    for concept in build_concept_library(100):
        ir = default_scene_ir(genre=concept.primary_id)
        names = [e.name for e in ir.entities]
        assert len(names) == len(set(names)), concept.pitch


def test_planner_rolls_concept_when_no_genre_match():
    import asyncio

    from src.agents.planner import PlannerAgent

    agent = PlannerAgent({})

    async def fake_llm(requirements, engine, genre_hint_text=""):
        assert "游戏概念" in genre_hint_text  # 融合概念的提示必须注入 LLM
        return {"tasks": [{"id": "task_001"}], "asset_plan": {}}

    agent._plan_with_llm = fake_llm
    result = asyncio.run(agent.plan({
        "project_context": {"requirements": "帮我做一个从来没见过的奇怪游戏", "engine": "godot"},
    }))
    assert result.get("genre")  # 融合概念的主基款
    assert result.get("representative")  # 概念卖点


def test_planner_still_uses_genre_match_when_hit():
    import asyncio

    from src.agents.planner import PlannerAgent

    agent = PlannerAgent({})
    captured = {}

    async def fake_llm(requirements, engine, genre_hint_text=""):
        captured["hint"] = genre_hint_text
        return {"tasks": [], "asset_plan": {}}

    agent._plan_with_llm = fake_llm
    result = asyncio.run(agent.plan({
        "project_context": {"requirements": "做一个贪吃蛇", "engine": "godot"},
    }))
    assert result.get("genre") == "snake"
    assert "贪吃蛇" in captured["hint"]
