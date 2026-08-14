"""场景生成端到端修复验证（离线）。

核心回归点：Python 侧 GodotSceneBuilder.build_tscn() 必须产出含真实 Godot
节点类型（CharacterBody2D / MeshInstance3D / Camera2D 等）的合法 .tscn，
且 GodotHTTPClient.send_scene 在有 tscn 文本时优先以 {"tscn":...} 发送，
从而让插件端直接落盘、绕开其类型错配逻辑。
"""

import asyncio

from src.engine.godot.godot_http_client import GodotHTTPClient
from src.engine.godot.scene_builder import GodotSceneBuilder


def test_build_tscn_produces_real_godot_nodes():
    desc = {
        "scene_name": "TestScene",
        "camera": {"orthographic": True, "position": [0, 0, 5]},
        "lighting": {"type": "directional", "intensity": 1.0},
        "game_objects": [
            {"name": "Player", "role": "player", "type": "CharacterBody2D",
             "position": [0, 0, 0], "components": [{"type": "PlayerController"}]},
            {"name": "Ground", "role": "ground", "type": "StaticBody2D",
             "position": [0, -10, 0]},
            {"name": "Cube", "type": "Cube", "position": [3, 0, 0],
             "components": [{"type": "Collider"}]},
        ],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)

    assert "[gd_scene" in tscn
    # role 映射生效
    assert 'type="CharacterBody2D"' in tscn
    assert 'type="StaticBody2D"' in tscn
    # 原始 Cube 形状映射到 MeshInstance3D（而非退化为空 Node2D）
    assert 'type="MeshInstance3D"' in tscn
    # 2D 相机 / 2D 灯光
    assert "Camera2D" in tscn
    assert "DirectionalLight2D" in tscn
    # 脚本组件引用
    assert "ExtResource" in tscn
    # 不应退化为空节点树：必须有真实类型节点（至少 Player/Ground/Cube）
    assert tscn.count("type=") >= 4


def test_build_tscn_detects_3d_dimension():
    desc = {
        "scene_name": "S3D",
        "camera": {"type": "Camera3D"},
        "game_objects": [
            {"name": "Hero", "role": "player_3d", "type": "CharacterBody3D"},
        ],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)

    assert "Node3D" in tscn
    assert "Camera3D" in tscn
    assert 'type="CharacterBody3D"' in tscn


def test_send_scene_uses_tscn_payload(monkeypatch):
    captured = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"status": "success", "scene_path": "res://scenes/X.tscn", "object_count": 0}

    class _FakeClient:
        async def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return _FakeResp()

    client = GodotHTTPClient()
    captured_client = _FakeClient()

    async def _fake_get_client():
        return captured_client

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    tscn_text = "[gd_scene load_steps=1 format=3]\n[node name=\"X\" type=\"Node2D\"]\n"
    result = asyncio.run(client.send_scene({"scene_name": "X"}, tscn_text=tscn_text))

    assert captured["path"] == "/api/scene/generate"
    assert captured["json"]["tscn"] == tscn_text
    assert captured["json"]["scene_name"] == "X"
    assert result["status"] == "success"


def test_send_scene_falls_back_to_desc(monkeypatch):
    captured = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"status": "success"}

    class _FakeClient:
        async def post(self, path, json=None):
            captured["json"] = json
            return _FakeResp()

    client = GodotHTTPClient()
    captured_client = _FakeClient()

    async def _fake_get_client():
        return captured_client

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    desc = {"scene_name": "Y", "game_objects": []}
    asyncio.run(client.send_scene(desc))  # 无 tscn_text

    assert "tscn" not in captured["json"]
    assert captured["json"] == desc
