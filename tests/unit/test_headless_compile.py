"""Headless 编译校验测试

验证 GameForge 可脱离 Godot 编辑器 GUI，直接调用 Godot 引擎二进制
（godot --headless --script）完成 GDScript 的语法校验与闭环修复。

无需打开编辑器、无需 8765 插件。

注意：真实引擎校验用例依赖本机存在 Godot 二进制；找不到时自动 skip，
不影响 CI。可用环境变量 GODOT_EDITOR_PATH 指定引擎路径。
"""
import os
import json
import glob
import asyncio

import pytest

from src.engine.godot import GodotEditor


def _discover_godot() -> str:
    candidates = [
        os.environ.get("GODOT_EDITOR_PATH"),
        r"D:/godot/Godot_v4.6.3-stable_win64.exe/Godot_v4.6.3-stable_win64.exe",
    ]
    candidates += glob.glob(r"D:/godot/*/Godot*.exe")
    candidates += glob.glob(r"C:/Users/*/AppData/Roaming/Godot*/Godot*.exe")
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


GODOT = _discover_godot()
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
NEED_ENGINE = bool(GODOT)

_engine_cfg = {
    "godot": {
        "editor_path": GODOT,
        "project_path": PROJECT_ROOT,
        "timeout": 120,
    }
}


def _write(path_rel: str, content: str) -> str:
    full = os.path.join(PROJECT_ROOT, path_rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


def _cleanup(*paths):
    for p in paths:
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1) 纯函数单测：错误解析（不依赖 Godot）
# ---------------------------------------------------------------------------

def test_parse_headless_errors_pairs_file_and_message():
    sample = (
        'SCRIPT ERROR: Parse Error: Expected expression for variable initial value after "=".\n'
        'ERROR: Failed to load script "res://scripts/player.gd" with error "Parse error".\n'
    )
    editor = GodotEditor(_engine_cfg)
    errors = editor._parse_headless_errors(sample)
    assert len(errors) == 1
    assert errors[0]["file"] == "res://scripts/player.gd"
    assert "Expected expression" in errors[0]["message"]
    assert errors[0]["type"] == "error"


def test_parse_headless_errors_empty_when_clean():
    editor = GodotEditor(_engine_cfg)
    assert editor._parse_headless_errors("GFCHECK_OK: res://scripts/ok.gd\n") == []


# ---------------------------------------------------------------------------
# 2) 真实引擎校验（需要本机 Godot）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not NEED_ENGINE, reason="未找到 Godot 引擎二进制")
def test_check_scripts_detects_syntax_error():
    rel = "scripts/_gf_htest.gd"
    full = _write(rel, "extends Node\nfunc _ready():\n    var x =\n        pass\n")
    try:
        editor = GodotEditor(_engine_cfg)
        result = editor.check_scripts(["res://" + rel])
        assert result.success is False
        assert len(result.errors) >= 1
        assert result.errors[0]["file"] == "res://" + rel
    finally:
        _cleanup(full,
                 os.path.join(PROJECT_ROOT, "_gf_check_manifest.json"),
                 os.path.join(PROJECT_ROOT, "_gf_check_result.json"))


@pytest.mark.skipif(not NEED_ENGINE, reason="未找到 Godot 引擎二进制")
def test_check_scripts_clean_after_fix():
    rel = "scripts/_gf_htest.gd"
    full = _write(rel, "extends Node\nfunc _ready():\n    var x = 1\n    print(x)\n")
    try:
        editor = GodotEditor(_engine_cfg)
        result = editor.check_scripts(["res://" + rel])
        assert result.success is True
        assert result.errors == []
    finally:
        _cleanup(full,
                 os.path.join(PROJECT_ROOT, "_gf_check_manifest.json"),
                 os.path.join(PROJECT_ROOT, "_gf_check_result.json"))


@pytest.mark.skipif(not NEED_ENGINE, reason="未找到 Godot 引擎二进制")
def test_check_scripts_no_engine_path_reports_error():
    # 用不存在的路径，避免被本机环境变量 GODOT_EDITOR_PATH 干扰
    cfg = {"godot": {"editor_path": "C:/nonexistent_godot.exe", "project_path": PROJECT_ROOT}}
    editor = GodotEditor(cfg)
    result = editor.check_scripts(["res://scripts/foo.gd"])
    assert result.success is False
    msg = result.errors[0]["message"]
    # 路径为空 -> 未配置；路径非空但文件不存在 -> 编辑器不存在
    assert ("未配置" in msg) or ("不存在" in msg) or ("Godot 编辑器" in msg)


# ---------------------------------------------------------------------------
# 3) 编译闭环路由：headless 模式 + 未配置引擎 -> 直接报错（不退回 HTTP）
# ---------------------------------------------------------------------------

def test_compile_loop_routing_headless_no_editor():
    # 直接验证路由分支：compile_mode=headless 且未配置引擎路径时，
    # 应直接 emit error 事件（不退回 8765 HTTP）。
    from src.core.graph import workflow as wf_mod

    # 构造最小 workflow 对象（避免实例化完整图，仅用于调用实例方法）
    wf = object.__new__(wf_mod.GameDevWorkflow)
    # 用不存在的引擎路径 -> GodotEditor.validate() 必返回 False（headless 模式直接报错）
    wf.config = {"godot": {"compile_mode": "headless", "editor_path": "C:/nonexistent_godot.exe"}}
    wf.log_error = lambda *a, **k: None

    events = []

    async def cb(kind, payload):
        events.append((kind, payload))

    async def _run():
        # 直接调用类方法（Python 3 中 Class.method 即函数，手动传入 self）
        await wf_mod.GameDevWorkflow._godot_compile_loop(
            wf, {"code_generated": {"scripts/x.gd": "extends Node"}},
            cb,
            max_rounds=1,
        )

    asyncio.run(_run())
    compile_events = [e for e in events if e[0] == "compile_result"]
    assert compile_events, "应产生 compile_result 事件"
    assert compile_events[0][1]["status"] == "error"
    assert "Godot 引擎路径" in compile_events[0][1]["message"]


# ---------------------------------------------------------------------------
# 4) 一键构建路由：headless 模式 + 未配置引擎 -> 直接 scene_skipped（不退回 HTTP）
# ---------------------------------------------------------------------------

def test_try_godot_pipeline_routing_headless_no_editor():
    # 验证 _try_godot_pipeline 在 compile_mode=headless 且未配置引擎路径时，
    # 应直接 emit scene_skipped 事件（不退回 8765 HTTP，避免依赖编辑器插件）。
    from src.core.graph import workflow as wf_mod

    wf = object.__new__(wf_mod.GameDevWorkflow)
    wf.config = {"godot": {"compile_mode": "headless", "editor_path": "C:/nonexistent_godot.exe"}}
    wf.log_error = lambda *a, **k: None

    events = []

    async def cb(kind, payload):
        events.append((kind, payload))

    async def _run():
        await wf_mod.GameDevWorkflow._try_godot_pipeline(
            wf, {"code_generated": {"scripts/x.gd": "extends Node"}}, cb
        )

    asyncio.run(_run())
    skipped = [e for e in events if e[0] == "scene_skipped"]
    assert skipped, "应产生 scene_skipped 事件"
    assert skipped[0][1]["reason"] == "godot_unavailable"
    # 不应产生任何依赖 8765 HTTP 的事件
    assert not any(e[0] == "scene_error" for e in events)
