"""P0-2 运行时冒烟测试。

不依赖真实 Godot 二进制，覆盖：
- 环境不可用时优雅降级（不阻塞生产）
- stderr 错误解析（含噪音过滤）
- 数据结构 + 结果序列化
"""
import os
import re

from src.engine.godot import runtime_smoke as rs


# ── 错误解析 ───────────────────────────────────────────────

def test_parse_runtime_errors_catches_script_error():
    stderr = (
        "Some startup log line\n"
        "ALSA lib pcm.c: ... (noisy)\n"
        "SCRIPT ERROR: Parse Error: Identifier 'foo' not declared in current scope.\n"
        "   at: res://scripts/player.gd:42\n"
        "Final line\n"
    )
    errs = rs._parse_runtime_errors(stderr)
    assert any("SCRIPT ERROR" in e["pattern"] for e in errs)
    assert not any("ALSA" in e["snippet"] for e in errs)


def test_parse_runtime_errors_filters_alsa_xlib_noise():
    stderr = (
        "ALSA lib confmisc.c:767:(parse_card) cannot find card '0'\n"
        "Xlib: extension \"XInputExtension\" missing\n"
        "MESA-LOADER: failed to open vgem\n"
        "WARNING: DisplayServer no main loop\n"
    )
    errs = rs._parse_runtime_errors(stderr)
    assert errs == []


def test_parse_runtime_errors_detects_failed_to_load():
    stderr = "Failed to load resource: res://assets/missing.png\n"
    errs = rs._parse_runtime_errors(stderr)
    assert any("Failed to load" in e["pattern"] for e in errs)


def test_parse_runtime_errors_detects_runtime_error():
    stderr = "RuntimeError: trying to call 'foo()' on null instance\n"
    errs = rs._parse_runtime_errors(stderr)
    assert any("RuntimeError" in e["pattern"] for e in errs)


# ── 环境不可用时降级 ───────────────────────────────────────────────

def test_skip_when_godot_binary_missing(tmp_path):
    config = {
        "godot": {
            "editor_path": "C:/nonexistent/godot.exe",
            "project_path": str(tmp_path),
        },
        "runtime_smoke": {"skip_when_unavailable": True, "frames": 10, "timeout_seconds": 5},
    }
    smoke = rs.GodotRuntimeSmoke(config)
    res = smoke.run_scene("res://scenes/Main.tscn")
    assert res.runnable is True, "降级时不应阻塞"
    assert res.exit_code == 0
    assert res.errors and res.errors[0]["pattern"] == "SKIPPED"


def test_fail_when_godot_binary_missing_and_skip_disabled(tmp_path):
    config = {
        "godot": {
            "editor_path": "C:/nonexistent/godot.exe",
            "project_path": str(tmp_path),
        },
        "runtime_smoke": {"skip_when_unavailable": False, "frames": 10, "timeout_seconds": 5},
    }
    smoke = rs.GodotRuntimeSmoke(config)
    res = smoke.run_scene("res://scenes/Main.tscn")
    assert res.runnable is False
    assert res.exit_code == -1
    assert res.errors and res.errors[0]["pattern"] == "ENV"


# ── 结果数据结构 ───────────────────────────────────────────────

def test_result_to_dict_contract():
    """P0-2 契约：complete 事件携带的 runnable 字段来源"""
    res = rs.RuntimeSmokeResult(
        runnable=True, exit_code=0,
        errors=[], warnings=[], frame_count=60,
        elapsed_seconds=1.234, scene_path="res://scenes/Main.tscn",
    )
    d = res.to_dict()
    assert d["runnable"] is True
    assert d["exit_code"] == 0
    assert d["scene_path"] == "res://scenes/Main.tscn"
    assert d["frame_count"] == 60
    assert d["elapsed_seconds"] == 1.234
    # 契约字段集：下游事件回调只读这些键
    assert set(d.keys()) == {
        "runnable", "exit_code", "errors", "warnings",
        "frame_count", "elapsed_seconds", "scene_path",
    }


def test_result_with_runtime_errors_is_not_runnable():
    res = rs.RuntimeSmokeResult(
        runnable=False, exit_code=1,
        errors=[{"pattern": "SCRIPT ERROR", "snippet": "Parse Error at player.gd:42"}],
        scene_path="res://scenes/Broken.tscn",
    )
    d = res.to_dict()
    assert d["runnable"] is False
    assert len(d["errors"]) == 1
    assert "player.gd:42" in d["errors"][0]["snippet"]


# ── frame_count 解析 ───────────────────────────────────────────────

def test_parse_frame_count_from_print():
    assert rs._parse_frame_count("frames: 60\n") == 60
    assert rs._parse_frame_count("frames=  120\n") == 120
    assert rs._parse_frame_count("nothing here") == 0


# ── 配置注入 ───────────────────────────────────────────────

def test_config_injection():
    """通过 config / env 都应生效，frames/timeout 从 runtime_smoke.* 取"""
    os.environ["GODOT_EDITOR_PATH"] = "C:/env_injected/godot.exe"
    os.environ["GODOT_PROJECT_PATH"] = "D:/env_injected/proj"
    try:
        config = {
            "godot": {"editor_path": "", "project_path": ""},  # 让 env 兜底
            "runtime_smoke": {"frames": 30, "timeout_seconds": 7},
        }
        smoke = rs.GodotRuntimeSmoke(config)
        assert smoke.frames == 30
        assert smoke.timeout == 7
        assert smoke.editor_path == "C:/env_injected/godot.exe"
        assert smoke.project_path == "D:/env_injected/proj"
    finally:
        os.environ.pop("GODOT_EDITOR_PATH", None)
        os.environ.pop("GODOT_PROJECT_PATH", None)