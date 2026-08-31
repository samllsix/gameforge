"""gd-guard Python 接线测试（二进制缺失时优雅降级；存在时走真实扫描）。"""
import os
import sys

import pytest

from src.engine.godot.gd_guard import scan_project
from src.engine.godot.scene_to_godot import default_scene_ir, write_project


def test_unavailable_when_binary_missing(monkeypatch, tmp_path):
    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "find_guard", lambda: None)
    r = scan_project(str(tmp_path))
    assert r["available"] is False
    assert r["verdict"] == "unavailable"


def _make_project(tmp_path):
    write_project(str(tmp_path), default_scene_ir(), width=320, height=180)
    # 生成一个"用户脚本"（非信任前缀）：内容干净
    user_script = tmp_path / "scripts" / "player_controller.gd"
    user_script.parent.mkdir(exist_ok=True)
    user_script.write_text(
        "extends CharacterBody2D\nfunc _physics_process(d):\n\tvelocity.x = 10\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(
    not __import__("src.engine.godot.gd_guard", fromlist=["find_guard"]).find_guard(),
    reason="gd-guard 未构建",
)
def test_clean_project_allowed(tmp_path):
    from src.engine.godot.gd_guard import find_guard

    _make_project(tmp_path)
    assert find_guard(), "gd-guard 未构建"
    r = scan_project(str(tmp_path))
    assert r["available"] and r["verdict"] == "allow", r["findings"]


@pytest.mark.skipif(
    not __import__("src.engine.godot.gd_guard", fromlist=["find_guard"]).find_guard(),
    reason="gd-guard 未构建",
)
def test_malicious_script_blocked(tmp_path):
    from src.engine.godot.gd_guard import find_guard

    _make_project(tmp_path)
    evil = tmp_path / "scripts" / "evil.gd"
    evil.write_text(
        "extends Node\nfunc _ready():\n\tOS.execute(\"cmd /c del *\", [])\n",
        encoding="utf-8",
    )
    r = scan_project(str(tmp_path))
    assert r["verdict"] == "block"
    assert any(f["rule"] == "OS.execute" for f in r["findings"])
    # trust 边界：官方运行时脚本即使含 FileAccess 也不拦截（screenshot_server 不在扫描内）


@pytest.mark.skipif(
    not __import__("src.engine.godot.gd_guard", fromlist=["find_guard"]).find_guard(),
    reason="gd-guard 未构建",
)
def test_tscn_ext_resource_escape_blocked(tmp_path):
    from src.engine.godot.gd_guard import find_guard

    _make_project(tmp_path)
    tscn = tmp_path / "scenes" / "main.tscn"
    content = tscn.read_text(encoding="utf-8")
    content = content.replace(
        'path="res://addons/gameforge/runtime/mover.gd"',
        'path="res://../secrets.gd"',
    )
    tscn.write_text(content, encoding="utf-8")
    r = scan_project(str(tmp_path))
    assert r["verdict"] == "block"
    assert any(f["rule"] == "ext_resource_escape" for f in r["findings"])


def test_export_gate_blocks_on_guard_findings(monkeypatch, tmp_path):
    """发布门禁第 0 关: gd-guard block → 502, stage=gd_guard, 不进入出包"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GAMEFORGE_ALLOW_INSECURE_LOCALHOST", "true")
    monkeypatch.setenv("GODOT_EDITOR_PATH", "D:/nonexistent/godot.exe")
    # 编辑器存在性检查需要一个真实存在的路径(门禁在它之后), 指向 python.exe 即可
    import src.api.main as _m

    monkeypatch.setattr(_m, "_resolve_editor_path", lambda: os.path.abspath(sys.executable))

    # 端点从仓库 projects/<id> 解析项目 → 使用真实存在的 demo_jump_v2
    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "find_guard", lambda: "C:/fake/gd-guard.exe")

    def fake_scan(project_path, timeout=120.0):
        return {
            "available": True,
            "verdict": "block",
            "findings": [
                {"file": "res://scripts/evil.gd", "line": 3,
                 "rule": "OS.execute", "detail": "执行任意系统命令",
                 "snippet": "OS.execute('cmd', [])"}
            ],
            "scanned": {"gd": 3, "tscn": 1, "project_godot": 1},
        }

    monkeypatch.setattr(gd_guard, "scan_project", fake_scan)
    # export_kit 若被调用即为缺陷(门禁必须先拦)
    from src.engine.godot import export_kit

    monkeypatch.setattr(
        export_kit, "export_project",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("门禁拦截后不应进入导出")),
    )

    client = TestClient(main_mod.app)
    r = client.post("/api/v1/projects/demo_jump_v2/export")
    assert r.status_code == 502
    body = r.json()
    assert body["stage"] == "gd_guard"
    assert "OS.execute" in body["errors"][0]["snippet"]


def test_export_gate_passes_when_allow(monkeypatch, tmp_path):
    """gd-guard allow → 门禁放行进入基线检查(基线也过) → 进入导出阶段"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GAMEFORGE_ALLOW_INSECURE_LOCALHOST", "true")
    monkeypatch.setenv("GODOT_EDITOR_PATH", "D:/nonexistent/godot.exe")
    # 编辑器存在性检查需要一个真实存在的路径(门禁在它之后), 指向 python.exe 即可
    import src.api.main as _m

    monkeypatch.setattr(_m, "_resolve_editor_path", lambda: os.path.abspath(sys.executable))

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "find_guard", lambda: "C:/fake/gd-guard.exe")
    monkeypatch.setattr(
        gd_guard, "scan_project",
        lambda *a, **k: {"available": True, "verdict": "allow", "findings": [],
                         "scanned": {"gd": 2, "tscn": 1}},
    )
    # 基线检查会过(项目由 write_project 生成); ensure_imported/冒烟需要真 Godot → 桩掉
    from src.engine.godot import export_kit

    monkeypatch.setattr(export_kit, "ensure_imported", lambda *a, **k: True)

    class _FakeSmoke:
        runnable = True
        errors: list = []

    from src.engine.godot import runtime_smoke

    monkeypatch.setattr(
        runtime_smoke.GodotRuntimeSmoke, "run_scene",
        lambda self, **k: _FakeSmoke(),
    )
    monkeypatch.setattr(
        export_kit, "export_project",
        lambda *a, **k: {"ok": True, "out_path": "x", "stderr_tail": ""},
    )

    client = TestClient(main_mod.app)
    r = client.post("/api/v1/projects/demo_jump_v2/export")
    assert r.status_code == 200
    assert r.json()["ok"] is True
