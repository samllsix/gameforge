"""测试 P1 语义级复用：RecipeStore 存储/检索 + workflow 快速路径。

不拉真实 LLM，直接用 RecipeStore 写入配方并验证命中/注入。
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from src.core.graph.workflow import GameDevWorkflow
from src.core.recipes import RecipeStore, _key_terms, _normalize_text


def _save_recipe_raw(store: RecipeStore, requirements: str, title: str = "Demo"):
    """手工构造一个已通过验证的 state 并保存为配方。"""
    state = {
        "runnable": True,
        "project_context": {"requirements": requirements},
        "game_design_model": {"game_title": title, "genre": "platformer"},
        "task_plan": [
            {"id": "task_001", "name": "实现Player控制器", "status": "completed"}
        ],
        "code_generated": {
            "res://scripts/player/player_controller.gd": "extends CharacterBody2D\n",
        },
        "scene_description": {"scene_name": "GameScene", "game_objects": []},
    }
    assert store.save_recipe(state) is True
    return state


def test_normalize_text():
    assert _normalize_text("  2D 平台 跳跃 游戏, 玩家! ") == "2d平台跳跃游戏玩家"


def test_key_terms_identical_family():
    a = _key_terms("制作一个2D平台跳跃游戏，含玩家、敌人、金币")
    b = _key_terms("弄个跑酷平台跳跃小游戏，有玩家、怪物、金币")
    assert len(a & b) >= 2, "同类需求应共享多个特征词"


def test_save_requires_verified():
    store = RecipeStore(storage_dir=tempfile.mkdtemp())
    state = {
        "runnable": None,  # 未验证
        "project_context": {"requirements": "随便"},
        "code_generated": {"a.gd": "x"},
    }
    assert store.save_recipe(state) is False


def test_search_exact_hit(tmp_path):
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "制作一个2D平台跳跃游戏，含玩家、敌人、金币")
    hit = store.search("制作一个2D平台跳跃游戏，含玩家、敌人、金币")
    assert hit is not None
    assert hit["title"] == "Demo"


def test_search_fuzzy_hit(tmp_path):
    """同类需求、不同措辞（怪物vs敌人），仍能模糊命中。"""
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "制作一个2D平台跳跃游戏，含玩家、敌人、金币", title="平台跳跃")
    hit = store.search("做一个2D平台跳跃游戏，有玩家、怪物、金币")
    assert hit is not None
    assert hit["title"] == "平台跳跃"


def test_search_no_hit_different_genre(tmp_path):
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "2D平台跳跃 玩家 敌人 金币")
    hit = store.search("塔防游戏 建塔 防守 敌人")
    assert hit is None


def test_apply_recipe_injects_state():
    store = RecipeStore(storage_dir=tempfile.mkdtemp())
    recipe = {
        "game_design_model": {"game_title": "T"},
        "task_plan": [{"id": "x", "status": "completed"}],
        "code_files": {"res://a.gd": "print(1)"},
        "scene_description": {"scene_name": "S"},
    }
    state = {"code_generated": {}}
    RecipeStore.apply_recipe(state, recipe)
    assert state["recipe_hit"] is True
    assert state["runnable"] is True
    assert state["code_generated"]["res://a.gd"] == "print(1)"


# ---------------- workflow 快速路径 ----------------

def _make_workflow() -> GameDevWorkflow:
    config = {
        "godot": {"editor_path": "", "project_path": "", "compile_mode": "auto"},
        "recipes": {"enabled": True},
        "llm": {"default_model": "stub"},
    }
    wf = GameDevWorkflow.__new__(GameDevWorkflow)
    wf.config = config
    wf.memory = None
    wf.recipe_enabled = True
    wf.recipe_store = RecipeStore(storage_dir=tempfile.mkdtemp())
    wf.logger = __import__("structlog").get_logger()
    return wf


async def test_workflow_recipe_hit_skips_main_graph():
    wf = _make_workflow()
    _save_recipe_raw(wf.recipe_store, "2D平台跳跃 玩家 敌人 金币", title="平台跳跃")

    called = {"post_process": False, "pipeline": False}

    async def fake_post(state, scene_task, event_callback=None):
        called["post_process"] = True

    async def fake_pipeline(state, event_callback=None):
        called["pipeline"] = True

    wf._post_process = fake_post
    wf._try_godot_pipeline = fake_pipeline

    events = []

    async def cb(ev, data):
        events.append(ev)

    state = {
        "project_context": {"requirements": "2D平台跳跃 玩家 敌人 金币"},
        "code_generated": {},
        "warnings": [],
    }
    result = await wf._run_recipe(state, cb)

    assert result is not None
    assert result["recipe_hit"] is True
    assert called["post_process"] is True
    assert "recipe_hit" in events


async def test_workflow_recipe_miss_returns_none():
    wf = _make_workflow()
    _save_recipe_raw(wf.recipe_store, "2D平台跳跃 玩家")
    state = {"project_context": {"requirements": "塔防 建塔 敌人"}, "code_generated": {}, "warnings": []}
    # 未命中：不应触发 post_process
    async def fake_post(state, scene_task, event_callback=None):
        raise AssertionError("不应进入后处理")
    wf._post_process = fake_post
    result = await wf._run_recipe(state, None)
    assert result is None


async def test_workflow_recipe_disabled_returns_none():
    wf = _make_workflow()
    wf.recipe_enabled = False
    _save_recipe_raw(wf.recipe_store, "2D平台跳跃")
    state = {"project_context": {"requirements": "2D平台跳跃"}, "code_generated": {}, "warnings": []}
    result = await wf._run_recipe(state, None)
    assert result is None


# ---------------- 匹配优先级 / 择优 ----------------

def test_search_exact_priority_over_fuzzy(tmp_path):
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "2D平台跳跃 玩家 敌人", title="A_平台")
    # 再存一个需求更接近的配方
    _save_recipe_raw(store, "制作一个2D平台跳跃游戏，含玩家、敌人、金币", title="B_完整平台")
    hit = store.search("制作一个2D平台跳跃游戏，含玩家、敌人、金币")
    assert hit is not None
    assert hit["title"] == "B_完整平台", "精确文本匹配应优先，而非较早存入的相近配方"


def test_fuzzy_picks_most_relevant_among_multiple(tmp_path):
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "塔防游戏 建塔 防守 敌人 金币", title="塔防")
    _save_recipe_raw(store, "2D平台跳跃 玩家 敌人 金币", title="平台")
    hit = store.search("做一个平台跳跃小游戏，含敌人、金币")
    assert hit is not None
    assert hit["title"] == "平台", "应从多个配方中选最贴合者"


# ---------------- 边界防御 ----------------

def test_search_single_feature_term_no_hit(tmp_path):
    """需求只含 1 个特征词（或没有），不应触发模糊命中，避免过泛误复用。"""
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "2D平台跳跃 玩家 敌人 金币")
    assert store.search("金币") is None
    assert store.search("随便写点什么") is None
    assert store.search("") is None
    assert store.search(None) is None


def test_save_recipe_rejects_unverified_branches(tmp_path):
    store = RecipeStore(storage_dir=str(tmp_path))
    for runnable, code in [(None, {"a.gd": "x"}), (False, {"a.gd": "x"}), (True, {})]:
        state = {"runnable": runnable, "project_context": {"requirements": "二D平台"}, "code_generated": code}
        assert store.save_recipe(state) is False, f"runnable={runnable} code={bool(code)}"


def test_apply_recipe_merges_existing_code_files():
    store = RecipeStore(storage_dir=tempfile.mkdtemp())
    recipe = {"code_files": {"res://new.gd": "new"}, "scene_description": None, "task_plan": [], "game_design_model": {}}
    state = {"code_generated": {"res://old.gd": "old"}, "warnings": []}
    RecipeStore.apply_recipe(state, recipe)
    assert state["code_generated"]["res://old.gd"] == "old", "已有文件不应被覆盖丢失"
    assert state["code_generated"]["res://new.gd"] == "new"


# ---------------- 持久化 ----------------

def test_recipe_persisted_and_key_fields(tmp_path):
    store = RecipeStore(storage_dir=str(tmp_path))
    _save_recipe_raw(store, "2D平台跳跃 玩家 敌人", title="持久化验证")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["verified"] is True
    assert data["title"] == "持久化验证"
    assert "res://scripts/player/player_controller.gd" in data["code_files"]


def test_same_text_different_code_produce_distinct_files(tmp_path):
    """同一需求、不同代码内容 → 文件名不同，避免互相覆盖。"""
    store = RecipeStore(storage_dir=str(tmp_path))
    s1 = {"runnable": True, "project_context": {"requirements": "平台跳跃"},
          "game_design_model": {}, "task_plan": [],
          "code_generated": {"a.gd": "v1"}, "scene_description": None}
    s2 = {"runnable": True, "project_context": {"requirements": "平台跳跃"},
          "game_design_model": {}, "task_plan": [],
          "code_generated": {"a.gd": "v2"}, "scene_description": None}
    assert store.save_recipe(s1) is True
    assert store.save_recipe(s2) is True
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_normalize_handles_punctuation_case_and_whitespace():
    from src.core.recipes import _normalize_text
    assert _normalize_text("  平台 跳跃，跑酷!! 玩家. ") == "平台跳跃跑酷玩家"
    assert _normalize_text("2D Platform Game") == "2dplatformgame"


# ---------------- 实时预览 project_id 解析 ----------------

def test_resolve_preview_project_id_from_project_name():
    """优先取 project_name，sanitize 后需符合 _PREVIEW_PROJECT_RE。"""
    wf = _make_workflow()
    state = {"project_context": {"project_name": "My Game Project", "requirements": "2D平台跳跃"}}
    pid = wf._resolve_preview_project_id(state)
    assert pid == "My_Game_Project", "非字母数字字符应替换为下划线"


def test_resolve_preview_project_id_falls_back_to_requirements():
    """project_name 缺失时，回退到 requirements。"""
    wf = _make_workflow()
    state = {"project_context": {"requirements": "制作一个2D平台跳跃游戏"}}
    pid = wf._resolve_preview_project_id(state)
    # sanitize: 中文字符 → _，再 strip 首尾 _, 中间连续字母数字保留
    assert pid == "2D", f"实际 sanitize 后应为 '2D'，实得 {pid!r}"
    assert all(c.isalnum() or c in "_-." for c in pid)


def test_resolve_preview_project_id_explicit_override_takes_priority():
    """显式注入的 preview_project_id 应被尊重。"""
    wf = _make_workflow()
    state = {
        "preview_project_id": "my-custom-pid",
        "project_context": {"project_name": "ignored", "requirements": "ignored"},
    }
    assert wf._resolve_preview_project_id(state) == "my-custom-pid"


def test_resolve_preview_project_id_illegal_returns_empty():
    """非法字符全部 sanitize 掉后为空时，返回空串（调用方据此不附 project_id）。"""
    wf = _make_workflow()
    state = {"project_context": {"project_name": "    ", "requirements": ""}}
    assert wf._resolve_preview_project_id(state) == ""


def test_resolve_preview_project_id_truncates_to_64():
    """超长名截到 64 字符以内。"""
    wf = _make_workflow()
    long_name = "a" * 100
    pid = wf._resolve_preview_project_id({"project_context": {"project_name": long_name}})
    assert len(pid) == 64