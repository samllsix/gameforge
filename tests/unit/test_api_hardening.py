"""接口规范修复的回归测试。

覆盖：导入路径穿越校验（schema + GodotEditor 纵深防御）、
限流前缀排除、认证白名单、统一错误信封、202+Location、
预览 scene 参数校验、请求指标落地。
"""
import json
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("GAMEFORGE_ALLOW_INSECURE_LOCALHOST", "true")

from src.api.middleware import RateLimitMiddleware, RequestMetricsMiddleware
from src.api.schemas import ImportRequest


# ── 1. ImportRequest 路径校验（schema 层） ────────────────────────────────────

def test_import_request_rejects_traversal():
    with pytest.raises(ValidationError):
        ImportRequest(files={"../../evil.txt": "x"})


def test_import_request_rejects_dotdot_segment():
    with pytest.raises(ValidationError):
        ImportRequest(files={"scripts/../../evil.txt": "x"})


def test_import_request_rejects_absolute_and_encoded():
    for bad in ["/etc/passwd", "C:/Windows/x.txt", "~/.ssh/id_rsa", "a%2e%2e%2fb"]:
        with pytest.raises(ValidationError):
            ImportRequest(files={bad: "x"})


def test_import_request_rejects_empty():
    with pytest.raises(ValidationError):
        ImportRequest(files={})
    with pytest.raises(ValidationError):
        ImportRequest(files={"": "x"})


def test_import_request_accepts_relative_paths():
    req = ImportRequest(files={
        "scripts/main.gd": "x",
        "res://scenes/Main.tscn": "y",
    })
    assert set(req.files) == {"scripts/main.gd", "res://scenes/Main.tscn"}


# ── 2. GodotEditor.import_files 纵深防御（引擎层兜底） ────────────────────────

def test_import_files_blocks_traversal_at_engine_level(tmp_path):
    from src.engine.godot import GodotEditor

    editor = GodotEditor({"godot": {"project_path": str(tmp_path)}})
    result = editor.import_files({
        "../outside.txt": "evil",
        "scripts/main.gd": "extends Node\n",
    })

    assert any("越出项目目录" in e for e in result["errors"])
    assert result["imported"] == ["scripts/main.gd"]
    assert not (tmp_path.parent / "outside.txt").exists()
    assert (tmp_path / "scripts" / "main.gd").read_text(encoding="utf-8") == "extends Node\n"


# ── 3. 限流中间件：前缀排除（预览轮询） ──────────────────────────────────────

def _asgi_app(recorder):
    async def app(scope, receive, send):
        recorder.append(scope["path"])
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})
    return app


async def _receive():
    return {"type": "http.request"}


async def _send(message):
    pass


async def test_rate_limit_blocks_over_limit():
    reached = []
    mw = RateLimitMiddleware(
        _asgi_app(reached), max_requests=2, window_seconds=60,
    )
    scope = {"type": "http", "path": "/other", "client": ("1.2.3.4", 1)}
    for _ in range(2):
        await mw(scope, _receive, _send)
    await mw(scope, _receive, _send)  # 第3次应被 429 拦截
    assert reached.count("/other") == 2


async def test_rate_limit_prefix_exclusion_allows_preview_polling():
    reached = []
    mw = RateLimitMiddleware(
        _asgi_app(reached), max_requests=2, window_seconds=60,
        exclude_prefixes=["/api/v1/preview/"],
    )
    # 先把同一 IP 的额度耗尽
    scope_other = {"type": "http", "path": "/other", "client": ("1.2.3.4", 1)}
    for _ in range(3):
        await mw(scope_other, _receive, _send)

    # 排除前缀内的预览轮询不受限额影响
    scope_preview = {"type": "http", "path": "/api/v1/preview/frame", "client": ("1.2.3.4", 1)}
    for _ in range(5):
        await mw(scope_preview, _receive, _send)
    assert reached.count("/api/v1/preview/frame") == 5


# ── 4. 请求指标中间件：log_file 参数真实落地 ─────────────────────────────────

async def test_request_metrics_writes_log_file(tmp_path):
    log_file = tmp_path / "metrics.jsonl"
    log_file.unlink(missing_ok=True)  # tmp_path 跨 pytest 运行复用，先清理
    reached = []
    mw = RequestMetricsMiddleware(_asgi_app(reached), log_file=str(log_file))
    scope = {"type": "http", "path": "/x", "method": "GET", "client": ("1.2.3.4", 1)}
    await mw(scope, _receive, _send)

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["path"] == "/x"
    assert entry["status_code"] == 200
    assert "duration_ms" in entry


# ── 5. 认证白名单：驾驶舱入口必须公开 ────────────────────────────────────────

def test_public_paths_include_dashboard_entries():
    from src.api.security import APIKeyAuthMiddleware
    for path in ["/dashboard", "/digital", "/demo"]:
        assert path in APIKeyAuthMiddleware.PUBLIC_PATHS, f"{path} 不在认证白名单"


# ── 6. 统一错误信封 + 202 + scene 校验（走真实 app，不触发 lifespan） ─────────

@pytest.fixture(scope="module")
def api_client():
    from fastapi.testclient import TestClient
    import src.api.main as main_mod
    return TestClient(main_mod.app)


def test_http_exception_returns_unified_envelope(api_client):
    r = api_client.get("/api/v1/task/!!!invalid!!!")
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "bad_request"
    assert "任务ID" in body["message"]


def test_validation_error_returns_unified_envelope(api_client):
    r = api_client.post("/api/v1/generate", json={
        "requirements": "做一个贪吃蛇", "engine": "unreal",
    })
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error"
    assert isinstance(body["fields"], list)


def test_generate_returns_202_with_location(api_client, monkeypatch):
    import src.api.main as main_mod

    class _DummyWorkflow:
        async def run(self, state):
            return {}

    monkeypatch.setattr(main_mod, "create_workflow", lambda cfg: _DummyWorkflow())

    r = api_client.post("/api/v1/generate", json={
        "requirements": "做一个贪吃蛇游戏", "engine": "godot",
    })
    assert r.status_code == 202
    assert r.headers.get("location", "").startswith("/api/v1/task/")
    assert r.json()["task_id"]


def test_preview_scene_param_rejects_traversal(api_client):
    r = api_client.get("/api/v1/preview/frame", params={
        "project_id": "demo", "scene": "res://../../evil.tscn",
    })
    assert r.status_code == 400


def test_preview_width_rejects_out_of_range(api_client):
    r = api_client.get("/api/v1/preview/frame", params={
        "project_id": "demo", "width": 99999,
    })
    assert r.status_code == 422


def test_preview_task_id_resolves_sandbox_path(api_client, tmp_path):
    """sandbox task_id 应解析到 data/sandbox/<project_id>/tasks/<task_id>/"""
    import os
    from src.api.main import _resolve_preview_project

    project_id = "demo"
    task_id = "task_123"
    sandbox_root = tmp_path / "data" / "sandbox" / project_id / "tasks" / task_id
    sandbox_root.mkdir(parents=True, exist_ok=True)
    (sandbox_root / "project.godot").write_text("[application]\n", encoding="utf-8")

    cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        resolved = _resolve_preview_project(project_id, task_id=task_id)
        assert os.path.isdir(resolved)
        assert resolved.endswith(os.path.join("data", "sandbox", project_id, "tasks", task_id))
    finally:
        os.chdir(cwd)


def test_preview_task_id_missing_returns_404(api_client):
    r = api_client.get("/api/v1/preview/frame", params={
        "project_id": "demo", "task_id": "task_missing",
    })
    assert r.status_code == 404
