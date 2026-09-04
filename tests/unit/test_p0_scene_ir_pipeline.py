"""P0-1 修复验证：工作流 Scene IR 落盘 → 预览端点读取 → 美术主题匹配。

覆盖链路：
1. scene_generator 三级生成均返回 (scene_desc, scene_ir) 元组
2. workflow 在 scene_complete 事件前把 IR 落盘到 projects/<pid>/.scene_ir.json
3. 预览端点 _load_project_scene_ir 读取落盘 IR（缺失/损坏回退 None）
4. art_director 需求关键词匹配主题包（太空→space_night，纠正硬编码海洋主题）
"""
import asyncio
import json
import os

import pytest

from src.agents.art_director import _find_pack_by_keywords, _resolve_pack, plan_art
from src.agents.scene_ir import SceneIR
from src.engine.godot.scene_to_godot import default_scene_ir


# ---------------------------------------------------------------------------
# 1) scene_generator：元组返回（Level 1 模板路径，无需 LLM）
# ---------------------------------------------------------------------------

def test_scene_generator_template_path_returns_ir():
    from src.agents.scene_generator import SceneGeneratorAgent

    agent = SceneGeneratorAgent({"godot": {}})
    gdm = {"genre": "shooter", "entities": [], "scenes": []}

    scene_desc, scene_ir = asyncio.run(
        agent._generate_scene_description("太空射击游戏", [], "godot", gdm, {})
    )

    assert scene_desc is not None
    assert isinstance(scene_ir, SceneIR)
    assert scene_ir.genre == "shooter"
    assert scene_ir.entities, "模板路径应产出实体蓝图"


def test_scene_generator_fallback_produces_genre_aware_ir():
    """LLM 失败时 Level 3 兜底也必须产出 IR（预览依赖落盘 IR，P0-1）。"""
    from src.agents.scene_generator import SceneGeneratorAgent

    agent = SceneGeneratorAgent({"godot": {}})
    # gdm 无法匹配模板且 LLM 不可用 → Level 3 品类感知兜底
    agent._generate_via_llm_ir = lambda *a, **kw: _async_none_tuple()

    scene_desc, scene_ir = asyncio.run(
        agent._generate_scene_description("做一个太空射击游戏，消灭外星敌阵", [], "godot", {}, {})
    )

    assert scene_desc is not None
    assert isinstance(scene_ir, SceneIR)
    assert scene_ir.genre == "shooter", "太空射击需求应兜底到 shooter 蓝图"


def test_scene_generator_last_resort_returns_none_ir(monkeypatch):
    """Level 3 也失败（Level 4 硬编码兜底）才允许无 IR。"""
    from src.agents.scene_generator import SceneGeneratorAgent
    from src.engine.godot import scene_to_godot

    agent = SceneGeneratorAgent({"godot": {}})
    agent._generate_via_llm_ir = lambda *a, **kw: _async_none_tuple()

    def _raise(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(scene_to_godot, "default_scene_ir", _raise)

    scene_desc, scene_ir = asyncio.run(
        agent._generate_scene_description("未知需求", [], "godot", {}, {})
    )

    assert scene_desc is not None
    assert scene_ir is None


async def _async_none_tuple():
    return None, None


# ---------------------------------------------------------------------------
# 2) workflow：Scene IR 落盘（先于 scene_complete 事件）
# ---------------------------------------------------------------------------

def _bare_workflow():
    from src.core.graph import workflow as wf_mod

    wf = object.__new__(wf_mod.GameDevWorkflow)
    wf.config = {"godot": {}}
    wf.log_action = lambda *a, **k: None
    wf.log_error = lambda *a, **k: None
    return wf


def test_persist_scene_ir_writes_wrapper(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    wf = _bare_workflow()
    ir = default_scene_ir(theme="space_black", genre="shooter")

    wf._persist_scene_ir(
        {"project_context": {"requirements": "做一个太空射击游戏"},
         "preview_project_id": "space_demo"},
        ir,
    )

    ir_path = tmp_path / "projects" / "space_demo" / ".scene_ir.json"
    assert ir_path.is_file()
    payload = json.loads(ir_path.read_text(encoding="utf-8"))
    assert payload["requirements"] == "做一个太空射击游戏"
    assert payload["scene_ir"]["genre"] == "shooter"
    assert payload["scene_ir"]["theme"] == "space_black"


def test_persist_scene_ir_skips_none_ir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    wf = _bare_workflow()

    wf._persist_scene_ir({"preview_project_id": "x"}, None)

    assert not (tmp_path / "projects").exists()


def test_run_scene_generation_persists_before_complete(monkeypatch, tmp_path):
    """IR 必须在 scene_complete 事件发出前落盘：前端收到 project_id 才开始轮询预览。"""
    monkeypatch.chdir(tmp_path)
    wf = _bare_workflow()
    ir = default_scene_ir(theme="space_black", genre="shooter")

    async def fake_execute(state, **kwargs):
        return {
            "scene_status": "skipped",
            "scene_skip_reason": "auto_build_disabled",
            "scene_description": {"game_objects": []},
            "scene_ir": ir,
            "message": "skipped",
        }

    wf.scene_generator = type("SG", (), {"execute": staticmethod(fake_execute)})()
    wf.main_reviewer = type("MR", (), {"review_game_design": staticmethod(lambda s: {})})()

    ir_path = tmp_path / "projects" / "space_demo" / ".scene_ir.json"
    seen = {"file_existed_at_event": None}

    async def cb(kind, payload):
        # skipped/built 两种路径都会在事件里带出 project_id，落盘须先于任一该事件
        if kind in ("scene_complete", "scene_skipped"):
            seen["file_existed_at_event"] = ir_path.is_file()

    state = {
        "project_context": {"requirements": "太空射击"},
        "preview_project_id": "space_demo",
        "code_generated": {},
    }
    asyncio.run(wf_mod_run_scene_generation(wf, state, cb))

    assert ir_path.is_file(), "IR 应落盘"
    assert seen["file_existed_at_event"] is True, "落盘须先于 scene_complete/scene_skipped 事件"


def wf_mod_run_scene_generation(wf, state, cb):
    from src.core.graph import workflow as wf_mod

    return wf_mod.GameDevWorkflow._run_scene_generation(wf, state, cb)


# ---------------------------------------------------------------------------
# 3) 预览端点：_load_project_scene_ir
# ---------------------------------------------------------------------------

def test_load_project_scene_ir_roundtrip(tmp_path):
    from src.api.main import _load_project_scene_ir

    payload = {
        "project_id": "space_demo",
        "requirements": "做一个太空射击游戏",
        "scene_ir": default_scene_ir(theme="space_black", genre="shooter").model_dump(),
    }
    (tmp_path / ".scene_ir.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    ir, requirements = _load_project_scene_ir(str(tmp_path))
    assert ir is not None
    assert ir.genre == "shooter"
    assert requirements == "做一个太空射击游戏"


def test_load_project_scene_ir_missing_file(tmp_path):
    from src.api.main import _load_project_scene_ir

    ir, requirements = _load_project_scene_ir(str(tmp_path))
    assert ir is None
    assert requirements == ""


def test_load_project_scene_ir_corrupt_file(tmp_path):
    from src.api.main import _load_project_scene_ir

    (tmp_path / ".scene_ir.json").write_text("{broken json", encoding="utf-8")

    ir, requirements = _load_project_scene_ir(str(tmp_path))
    assert ir is None
    assert requirements == ""


# ---------------------------------------------------------------------------
# 4) art_director：需求关键词匹配主题包
# ---------------------------------------------------------------------------

def test_keyword_match_space_shooter():
    pack = _find_pack_by_keywords("做一个太空射击游戏，消灭外星敌阵")
    assert pack is not None
    assert pack["id"] == "space_night"


def test_keyword_match_ocean():
    pack = _find_pack_by_keywords("深海遗迹探险，躲开鲨鱼")
    assert pack is not None
    assert pack["id"] == "ocean"


def test_keyword_match_english():
    pack = _find_pack_by_keywords("A space invaders style shooter")
    assert pack is not None
    assert pack["id"] == "space_night"


def test_keyword_match_none_for_unrelated():
    assert _find_pack_by_keywords("随便一个游戏") is None


def test_plan_art_requirements_rescue_space_theme(monkeypatch):
    """theme 为游戏标题（匹配不到包）时，需求关键词纠偏到 space_night。"""
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    ir = default_scene_ir(theme="星际射手", genre="shooter")

    plan = plan_art(ir, requirements="做一个太空射击游戏")

    # space_night 槽位名词：astronaut / alien drone / metal deck
    assert "astronaut" in plan["player"]
    assert "alien drone" in plan["enemy"]
    assert "metal deck" in plan["ground"]


def test_plan_art_without_requirements_keeps_generic(monkeypatch):
    monkeypatch.setenv("GAMEFORGE_SMART_PROMPTS", "0")
    ir = default_scene_ir(theme="星际射手", genre="shooter")

    plan = plan_art(ir)

    assert len(plan) == 9
    assert "hero character" in plan["player"]


def test_resolve_pack_priority_id_over_keywords():
    ir = default_scene_ir(theme="farm", genre="platformer")
    pack = _resolve_pack(ir, requirements="太空射击")
    assert pack["id"] == "farm", "theme 精确命中包 id 时优先"


def test_resolve_pack_keywords_over_palette():
    """sky_blue 被 ocean/snow 共享：需求关键词应胜过调色板歧义匹配。"""
    ir = default_scene_ir(theme="sky_blue", genre="platformer")

    pack = _resolve_pack(ir, requirements="雪原极光下的平台跳跃")
    assert pack["id"] == "snow"

    pack = _resolve_pack(ir, requirements="深海平台跳跃")
    assert pack["id"] == "ocean"


def test_resolve_pack_genre_default_last():
    ir = SceneIR(genre="shooter")  # 无 theme、无需求
    pack = _resolve_pack(ir, requirements=None)
    assert pack["id"] == "space_night"


def test_resolve_pack_theme_title_falls_to_keyword():
    """模板路径把 theme 设为游戏标题：id/palette 均未命中 → 关键词兜底。"""
    ir = default_scene_ir(theme="星际远征", genre="shooter")
    pack = _resolve_pack(ir, requirements="太空射击")
    assert pack["id"] == "space_night"
