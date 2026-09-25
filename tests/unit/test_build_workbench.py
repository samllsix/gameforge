"""可玩构建工作台的版本化导出与原生会话测试。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.godot.export_kit import export_web_build, get_web_build
from src.engine.godot.supervisor import GodotSupervisor


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    project.mkdir(exist_ok=True)
    (project / "project.godot").write_text(
        '[application]\nconfig/name="demo"\n', encoding="utf-8"
    )
    return project


def test_web_build_writes_manifest_and_current_atomically(tmp_path, monkeypatch):
    project = _project(tmp_path)

    def fake_run(cmd, **kwargs):
        output = Path(cmd[-1])
        output.write_text("<html></html>", encoding="utf-8")
        output.with_suffix(".js").write_text("console.log('ok')", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("src.engine.godot.export_kit.subprocess.run", fake_run)
    result = export_web_build(str(project), "godot")

    assert result["ok"] is True
    assert result["manifest"]["entry_path"] == "index.html"
    assert {item["path"] for item in result["manifest"]["files"]} == {
        "index.html",
        "index.js",
    }
    current = get_web_build(str(project))
    assert current is not None
    assert current["build_id"] == result["build_id"]


def test_failed_web_build_preserves_current_manifest(tmp_path, monkeypatch):
    project = _project(tmp_path)
    root = project / ".gameforge" / "builds" / "web"
    root.mkdir(parents=True, exist_ok=True)
    old_manifest = {
        "build_id": "old",
        "entry_path": "index.html",
        "target": "Web",
        "files": [],
    }
    (root / "current.json").write_text(json.dumps(old_manifest), encoding="utf-8")
    monkeypatch.setattr(
        "src.engine.godot.export_kit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="export failed"),
    )
    result = export_web_build(str(project), "godot")
    assert result["ok"] is False
    assert get_web_build(str(project)) == old_manifest


def test_play_build_routes_return_current_versioned_build(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import src.api.main as main_mod

    project = _project(tmp_path)
    build_id = "20260907T120000Z-abc123def0"
    build_dir = project / ".gameforge" / "builds" / "web" / build_id
    build_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "build_id": build_id,
        "entry_path": "index.html",
        "target": "Web",
        "files": [],
    }
    (build_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (build_dir.parent / "current.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    monkeypatch.setattr(main_mod, "_resolve_preview_project", lambda _pid: str(project))
    client = TestClient(main_mod.app)

    current = client.get("/api/v1/projects/demo/play/build")
    assert current.status_code == 200
    assert current.json()["build"]["build_id"] == build_id
    assert current.json()["play_url"] == "/play/demo/index.html"

    versioned = client.get(f"/api/v1/projects/demo/builds/web/{build_id}")
    assert versioned.status_code == 200
    assert versioned.json()["play_url"].endswith(f"build_id={build_id}")

    invalid = client.get("/api/v1/projects/demo/builds/web/../../outside")
    assert invalid.status_code in {400, 404}


@pytest.mark.asyncio
async def test_native_session_uses_path_and_owns_only_its_process(
    tmp_path, monkeypatch
):
    project = _project(tmp_path)
    editor = tmp_path / "godot.exe"
    editor.write_text("", encoding="utf-8")
    calls = []

    class FakeProcess:
        pid = 42

        def __init__(self):
            self.returncode = None
            self.stdout = None
            self.stderr = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr("src.engine.godot.supervisor.subprocess.Popen", fake_popen)
    sup = GodotSupervisor({"godot": {"editor_path": str(editor)}})
    started = await sup.start_native("demo", str(project), "res://main.tscn")
    assert calls == [[str(editor), "--path", str(project), "res://main.tscn"]]
    assert started["managed"] is True and started["status"] == "running"
    stopped = await sup.stop_native("demo")
    assert stopped["managed"] is True
    assert (await sup.native_status("unknown"))["managed"] is False
