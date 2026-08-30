"""游戏品类规格库测试：注册表完整性、智能匹配、难度分级、蓝图、planner 注入。"""
from src.agents.genre_specs import (
    GENRE_SPECS,
    SUPPORTED_GENRE_IDS,
    UNIVERSAL_BASELINE,
    GenreSpec,
    build_genre_prompt_hint,
    get_spec,
    infer_difficulty,
    match_genre,
    scale_count,
    simplify_scope,
)
from src.agents.scene_ir import SceneIR


# ── 注册表完整性：市场主流品类各锚定一款代表作 ────────────────────────────────

EXPECTED_GENRES = {
    "platformer", "shooter", "runner", "puzzle", "snake", "breakout",
    "flappy", "tower_defense", "rpg", "farming_sim", "survivors", "racing",
    "match3", "sokoban", "minesweeper", "merge_2048", "pong", "rhythm",
    "clicker", "word_guess",
}


def test_registry_covers_market_genres():
    assert set(GENRE_SPECS) == EXPECTED_GENRES
    assert len(GENRE_SPECS) == 20


def test_every_spec_is_complete():
    for gid, spec in GENRE_SPECS.items():
        assert spec.id == gid
        assert spec.name_zh and spec.representative, gid
        assert spec.core_loop, gid
        assert len(spec.mechanics) >= 3, f"{gid} 核心机制不足"
        assert len(spec.extensions) >= 3, f"{gid} 拓展方向不足"
        assert spec.win_condition and spec.lose_condition, gid
        assert len(spec.keywords) >= 3, gid
        assert spec.entities, gid
        # 蓝图必须有玩家（或明确的操作载体）
        roles = {e["role"] for e in spec.entities}
        assert "player" in roles, gid
        # 蓝图实体字段齐全
        for e in spec.entities:
            assert set(e) == {"name", "role", "count", "spawn_zone"}, gid


def test_baseline_merges_universal_and_mechanics():
    for spec in GENRE_SPECS.values():
        assert spec.baseline == UNIVERSAL_BASELINE + spec.mechanics
    assert len(UNIVERSAL_BASELINE) >= 9  # 常规开发流程基本功能底线


def test_supported_genre_ids_sync_with_registry():
    assert set(SUPPORTED_GENRE_IDS) == set(GENRE_SPECS.keys())


# ── 难度分级：间接（分期）或直接生成简易版成品 ────────────────────────────────

def test_infer_difficulty_keywords():
    assert infer_difficulty("做一个简单的贪吃蛇练练手") == "easy"
    assert infer_difficulty("快速来一个试玩 demo") == "easy"
    assert infer_difficulty("做一个完整的复杂 RPG，有 boss 战") == "hard"
    assert infer_difficulty("做一个贪吃蛇") == "medium"
    assert infer_difficulty("") == "medium"


def test_simplify_scope_tiers():
    spec = GENRE_SPECS["platformer"]
    easy = simplify_scope(spec, "easy")
    med = simplify_scope(spec, "medium")
    hard = simplify_scope(spec, "hard")
    assert easy["extensions"] == [] and "简易可玩成品" in easy["scope_note"]
    assert easy["mode"] == "direct_mvp"
    assert len(med["extensions"]) == 1
    assert len(hard["extensions"]) == 3 and hard["mode"] == "staged"
    assert easy["entity_scale"] < med["entity_scale"] < hard["entity_scale"]


def test_scale_count_rounding():
    assert scale_count(3, 0.6) == 2   # easy 减量
    assert scale_count(3, 1.0) == 3
    assert scale_count(3, 1.4) == 5   # hard 加量
    assert scale_count(1, 0.6) == 1   # 最少保 1


def test_difficulty_scales_scene_entities():
    from src.engine.godot.scene_to_godot import default_scene_ir

    easy = default_scene_ir(genre="shooter", difficulty="easy")
    hard = default_scene_ir(genre="shooter", difficulty="hard")
    assert len(easy.entities) < len(hard.entities)
    assert easy.difficulty == "easy" and hard.difficulty == "hard"
    # 玩家在任何难度下都保留
    assert any(e.role == "player" for e in easy.entities)


def test_hint_reflects_difficulty_scope():
    easy_hint = build_genre_prompt_hint("贪吃蛇", difficulty="easy")
    assert "简易可玩成品" in easy_hint
    assert "分期" not in easy_hint

    hard_hint = build_genre_prompt_hint("贪吃蛇", difficulty="hard")
    assert "分期" in hard_hint


def test_hint_auto_infers_difficulty_from_requirements():
    hint = build_genre_prompt_hint("来一个简单的贪吃蛇")
    assert "简易可玩成品" in hint


# ── 智能匹配：需求 → 品类 ────────────────────────────────────────────────────

def test_match_chinese_requirements():
    assert match_genre("做一个马里奥那样的横版跳跃游戏").id == "platformer"
    assert match_genre("想要一个贪吃蛇小游戏").id == "snake"
    assert match_genre("植物大战僵尸那种塔防").id == "tower_defense"
    assert match_genre("俄罗斯方块").id == "puzzle"
    assert match_genre("像星露谷物语的农场种田游戏").id == "farming_sim"
    assert match_genre("吸血鬼幸存者那样的割草").id == "survivors"


def test_match_english_requirements():
    assert match_genre("a flappy bird clone").id == "flappy"
    assert match_genre("space invaders shooter").id == "shooter"
    assert match_genre("endless runner like subway surfers").id == "runner"


def test_match_no_hit_returns_none():
    assert match_genre("") is None
    assert match_genre("帮我写一个网站") is None


def test_get_spec_fallback_to_platformer():
    assert get_spec("snake").id == "snake"
    assert get_spec("nonexistent_genre").id == "platformer"
    assert get_spec(None).id == "platformer"


# ── 蓝图：规格 → SceneIR（全品类可生成）──────────────────────────────────────

def test_every_genre_produces_valid_scene_ir():
    from src.engine.godot.scene_to_godot import default_scene_ir

    for gid in GENRE_SPECS:
        ir = default_scene_ir(genre=gid)
        assert isinstance(ir, SceneIR)
        assert ir.genre == gid
        names = [e.name for e in ir.entities]
        assert len(names) == len(set(names)), f"{gid} 蓝图实体重名: {names}"


def test_platformer_blueprint_is_classic_mario_setup():
    from src.engine.godot.scene_to_godot import default_scene_ir

    ir = default_scene_ir(genre="platformer")
    roles = {e.role for e in ir.entities}
    assert {"player", "ground", "platform", "pickup", "enemy"} <= roles


def test_farming_blueprint_has_crops_and_npc():
    from src.engine.godot.scene_to_godot import default_scene_ir

    ir = default_scene_ir(genre="farming_sim")
    names = [e.name for e in ir.entities]
    assert any("Crop" in n for n in names)
    assert any("Villager" in n for n in names)


# ── LLM 提示注入 ─────────────────────────────────────────────────────────────

def test_prompt_hint_contains_spec_and_baseline():
    hint = build_genre_prompt_hint("做一个马里奥跳跃游戏")
    assert "横版平台跳跃" in hint
    assert "超级马里奥兄弟" in hint
    assert "基本功能基线" in hint
    assert "胜利条件" in hint


def test_prompt_hint_picks_limited_extensions():
    hint = build_genre_prompt_hint("马里奥跳跃", max_extensions=2)
    assert hint.count("- ") >= 2  # 至少机制+拓展条目


def test_prompt_hint_empty_when_no_match():
    assert build_genre_prompt_hint("写个网站") == ""


# ── planner 接线：返回 genre 字段 ────────────────────────────────────────────

def test_planner_plan_attaches_genre_match():
    import asyncio

    from src.agents.planner import PlannerAgent

    agent = PlannerAgent({})
    # 打桩 LLM 兜底路径，保证单元测试确定性（不发起真实网络调用）
    async def fake_llm(requirements, engine, genre_hint_text=""):
        return {"tasks": [{"id": "task_001", "name": "t"}], "asset_plan": {}}

    agent._plan_with_llm = fake_llm
    result = asyncio.run(agent.plan({
        "project_context": {"requirements": "做一个像星露谷的种田游戏", "engine": "godot"},
    }))
    assert result.get("genre") == "farming_sim"
    assert result.get("difficulty") == "medium"
    assert "Stardew" in (result.get("representative") or "")
