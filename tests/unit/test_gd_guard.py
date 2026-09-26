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


def _export_fixture(monkeypatch, tmp_path):
    """在临时目录造一个自足项目，并把端点解析到那里。

    不能直接用仓库里的 projects/demo_jump_v2：它的 AI 素材 assets/gen/*.png
    被 .gitignore 忽略，新克隆的仓库里不存在，基线检查 ext_resources 必挂。
    write_project 会落全运行时脚本 / main.tscn / sfx / icon / 导出预设，
    无 AI key 时用纯色块视觉，因此 ext_resource 只引用真实存在的运行时脚本。
    """
    from src.core import paths

    monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
    project = tmp_path / "projects" / "demo_jump_v2"
    write_project(str(project), default_scene_ir(), width=320, height=180)
    monkeypatch.setenv("GAMEFORGE_ALLOW_INSECURE_LOCALHOST", "true")
    monkeypatch.setenv("GODOT_EDITOR_PATH", "D:/nonexistent/godot.exe")
    # 编辑器存在性检查需要一个真实存在的路径(门禁在它之后), 指向 python.exe 即可
    import src.api.main as _m

    monkeypatch.setattr(_m, "_resolve_editor_path", lambda: os.path.abspath(sys.executable))
    return project


def test_export_gate_blocks_on_guard_findings(monkeypatch, tmp_path):
    """发布门禁第 0 关: gd-guard block → 502, stage=gd_guard, 不进入出包"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    _export_fixture(monkeypatch, tmp_path)

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

    _export_fixture(monkeypatch, tmp_path)

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "find_guard", lambda: "C:/fake/gd-guard.exe")
    monkeypatch.setattr(
        gd_guard, "scan_project",
        lambda *a, **k: {"available": True, "verdict": "allow", "findings": [],
                         "scanned": {"gd": 2, "tscn": 1}},
    )
    # ensure_imported/冒烟需要真 Godot → 桩掉
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
    # Web 预设走 export_web_build（版本化构建），需一并桩掉
    monkeypatch.setattr(
        export_kit, "export_web_build",
        lambda *a, **k: {
            "ok": True,
            "out_path": "x",
            "stderr_tail": "",
            "build_id": "test-build",
            "manifest": {"build_id": "test-build"},
        },
    )

    client = TestClient(main_mod.app)
    r = client.post("/api/v1/projects/demo_jump_v2/export")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ---------------- M5-02：闸门失效显式化 ----------------

def test_find_guard_prefers_ci_bin_dir(monkeypatch, tmp_path):
    """CI 预编译 bin/<平台>/ 优先于本地 target/ 构建产物。"""
    import shutil

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "_repo_root", tmp_path)
    # tmp_path 跨运行复用（确定性 basetemp），先清残留再搭场景
    shutil.rmtree(tmp_path / "tools", ignore_errors=True)
    name = gd_guard._guard_binary_names()[0]
    bin_dir = tmp_path / "tools" / "gd-guard" / "bin" / gd_guard._GUARD_BIN_PLATFORM_DIR
    bin_dir.mkdir(parents=True)
    (bin_dir / name).write_text("ci", encoding="utf-8")
    target_dir = tmp_path / "tools" / "gd-guard" / "target" / "release"
    target_dir.mkdir(parents=True)
    (target_dir / name).write_text("local", encoding="utf-8")

    assert gd_guard.find_guard() == str(bin_dir / name)


def test_find_guard_falls_back_to_target(monkeypatch, tmp_path):
    """无 CI 二进制时退回本地 cargo build 产物。"""
    import shutil

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "_repo_root", tmp_path)
    shutil.rmtree(tmp_path / "tools", ignore_errors=True)
    name = gd_guard._guard_binary_names()[0]
    target_dir = tmp_path / "tools" / "gd-guard" / "target" / "release"
    target_dir.mkdir(parents=True)
    (target_dir / name).write_text("local", encoding="utf-8")

    assert gd_guard.find_guard() == str(target_dir / name)


def test_health_exposes_guard_unavailable(monkeypatch):
    """M5-02：二进制缺失时 /health 必须显式报告 gd_guard_available=false。"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "find_guard", lambda: None)

    client = TestClient(main_mod.app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["gd_guard_available"] is False
    assert body["gd_guard_binary"] == ""


def test_health_exposes_guard_binary(monkeypatch):
    """M5-02：二进制在位时 /health 报告 available=true 与路径。"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "find_guard", lambda: "C:/fake/gd-guard.exe")

    client = TestClient(main_mod.app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["gd_guard_available"] is True
    assert body["gd_guard_binary"] == "C:/fake/gd-guard.exe"


# ---------------- M5-03：预览端点接闸门 ----------------

def test_preview_frame_blocked_by_guard(monkeypatch, tmp_path):
    """M5-03：含危险 API 的项目 GET /preview/frame → 403，Godot 进程不被拉起。"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    _export_fixture(monkeypatch, tmp_path)

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "scan_project", lambda *a, **k: {
        "available": True, "verdict": "block",
        "findings": [{"file": "res://scripts/evil.gd", "line": 3, "rule": "OS.execute",
                      "detail": "执行任意系统命令", "snippet": "OS.execute('cmd', [])"}],
        "scanned": {},
    })

    # 闸门必须先拦：supervisor 一旦被拉起即为缺陷
    from src.engine.godot import GodotSupervisor

    async def _explode(cfg):
        raise AssertionError("闸门拦截后不应拉起 Godot 进程")

    monkeypatch.setattr(GodotSupervisor, "get_instance", _explode)

    client = TestClient(main_mod.app)
    r = client.get("/api/v1/preview/frame", params={"project_id": "demo_jump_v2"})
    assert r.status_code == 403
    # 全局 HTTPException 处理器统一信封 {"error": ..., "message": detail}
    body = r.json()
    assert body["error"] == "forbidden"
    assert body["message"]["stage"] == "gd_guard"
    assert "OS.execute" in body["message"]["findings"][0]["snippet"]


def test_preview_frame_passes_when_guard_allows(monkeypatch, tmp_path):
    """M5-03：闸门 allow 时预览正常走 supervisor 渲染（不误伤正常流程）。"""
    import src.api.main as main_mod
    from fastapi.testclient import TestClient

    _export_fixture(monkeypatch, tmp_path)

    from src.engine.godot import gd_guard

    monkeypatch.setattr(gd_guard, "scan_project", lambda *a, **k: {
        "available": True, "verdict": "allow", "findings": [], "scanned": {},
    })

    from src.engine.godot import GodotSupervisor

    class _FakeSupervisor:
        async def is_alive(self, pid):
            return True

        async def get_frame(self, pid, frame_index=0, width=640, height=360):
            return b"\x89PNG fake frame"

    async def _fake_get_instance(cfg):
        return _FakeSupervisor()

    monkeypatch.setattr(GodotSupervisor, "get_instance", _fake_get_instance)

    client = TestClient(main_mod.app)
    r = client.get("/api/v1/preview/frame", params={"project_id": "demo_jump_v2"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["X-Preview-Source"] == "godot-mss"
