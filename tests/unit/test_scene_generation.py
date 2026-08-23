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


def test_build_tscn_2d_scene_no_3d_materials():
    """P0-1：2D 场景禁止出现 StandardMaterial3D — 用 2D 原生 ColorRect 替代。

    原因：MeshInstance2D + StandardMaterial3D 是 Godot 4 渲染管线的非法组合，
    会导致场景无法加载/渲染异常。
    """
    desc = {
        "scene_name": "Pure2D",
        "camera": {"orthographic": True, "background_color": [0.2, 0.4, 0.8, 1.0]},
        "lighting": {"type": "directional", "intensity": 1.0},
        "game_objects": [
            {"name": "Player", "role": "player", "type": "CharacterBody2D",
             "position": [0, 0, 0], "color": [1.0, 0.5, 0.2, 1.0]},
            {"name": "Ground", "role": "ground", "type": "StaticBody2D",
             "position": [0, -10, 0], "color": [0.4, 0.3, 0.2, 1.0]},
        ],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)

    assert "[gd_scene" in tscn
    # 2D 场景中不应出现 StandardMaterial3D（3D 材质）
    assert "StandardMaterial3D" not in tscn, (
        "P0-1 违规：2D 场景仍产出 StandardMaterial3D，"
        "请用 ColorRect 替代 _build_background/_build_visual_node 的 2D 分支。"
    )
    # 必须出现 2D 原生可视化节点
    assert "ColorRect" in tscn, "2D 场景应使用 ColorRect 作为可视化节点"
    # 仍然保留正确的 2D 物理/相机节点
    assert 'type="CharacterBody2D"' in tscn
    assert 'type="StaticBody2D"' in tscn
    assert "Camera2D" in tscn


def test_build_tscn_2d_background_fills_viewport():
    """2D 背景的 ColorRect 必须铺满整个 viewport，不能 0 像素。

    之前的实现：anchor_right=1.0/anchor_bottom=1.0 + offset_right=0，
    父节点是 Node2D，anchor_* 被忽略，最终 offset_right=offset_left=0，矩形宽=0。
    修复：用显式 offset 算出 viewport 尺寸（中心对齐 (0,0)）。
    """
    desc = {
        "scene_name": "BackgroundTest",  # 不用 "Background" 避免与内置节点名撞
        "camera": {
            "orthographic": True,
            "background_color": [0.2, 0.4, 0.8, 1.0],
            "viewport_size": [640, 360],
        },
        "game_objects": [],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)

    # 抽出 Background 节点块
    lines = tscn.splitlines()
    bg_idx = next(i for i, ln in enumerate(lines) if 'name="Background"' in ln)
    block = []
    for ln in lines[bg_idx + 1:]:
        if ln.startswith('[node ') or not ln.strip():
            break
        block.append(ln)
    text = "\n".join(block)
    # 必须有 offset_* 实际值（不是 0）
    import re
    offset_right = float(re.search(r'offset_right\s*=\s*([\-\d.]+)', text).group(1))
    offset_left = float(re.search(r'offset_left\s*=\s*([\-\d.]+)', text).group(1))
    offset_bottom = float(re.search(r'offset_bottom\s*=\s*([\-\d.]+)', text).group(1))
    offset_top = float(re.search(r'offset_top\s*=\s*([\-\d.]+)', text).group(1))
    width = offset_right - offset_left
    height = offset_bottom - offset_top
    # 矩形的宽高 = viewport 尺寸
    assert width == 640.0, f"背景宽度应等于 viewport 宽，实际 {width}"
    assert height == 360.0, f"背景高度应等于 viewport 高，实际 {height}"
    # Background 不应设 anchor_right=1（Node2D 父下被忽略，会误导后续读者）
    assert 'anchor_right = 1.0' not in text
    assert 'anchor_bottom = 1.0' not in text


def test_build_tscn_2d_background_falls_back_to_default_viewport():
    """未指定 viewport_size 时使用默认值（640x360）"""
    desc = {
        "scene_name": "Fallback",
        "camera": {"orthographic": True, "background_color": [0.5, 0.5, 0.5, 1.0]},
        "game_objects": [],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)
    import re
    m = re.search(r'offset_right\s*=\s*([\-\d.]+)', tscn)
    assert m and float(m.group(1)) == 320.0  # 640/2


def test_build_tscn_entity_visual_size_matches_scale():
    """实体可视矩形宽高 = entity.scale，避免撞上 0 像素问题。"""
    desc = {
        "scene_name": "EntitySizing",
        "camera": {"orthographic": True, "background_color": [0.0, 0.0, 0.0, 1.0]},
        "game_objects": [
            {"name": "Player", "role": "player", "type": "CharacterBody2D",
             "position": [0, 0, 0], "scale": [2, 3, 1], "color": [1, 0, 0, 1]},
            {"name": "Enemy", "role": "enemy", "type": "CharacterBody2D",
             "position": [5, 1, 0], "scale": [1, 1, 1], "color": [0, 1, 0, 1]},
            {"name": "Coin", "role": "pickup", "type": "Area2D",
             "position": [-3, 2, 0], "scale": [0.5, 0.5, 1], "color": [1, 1, 0, 1]},
        ],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)
    import re
    # 抽出每个 [node name="Mesh" ...] 块
    lines = tscn.splitlines()
    for i, ln in enumerate(lines):
        if 'name="Mesh" type="ColorRect"' not in ln:
            continue
        block = []
        for j in range(i + 1, len(lines)):
            if lines[j].startswith('[node ') or not lines[j].strip():
                break
            block.append(lines[j])
        text = "\n".join(block)
        offset_left = float(re.search(r'offset_left\s*=\s*([\-\d.]+)', text).group(1))
        offset_right = float(re.search(r'offset_right\s*=\s*([\-\d.]+)', text).group(1))
        offset_top = float(re.search(r'offset_top\s*=\s*([\-\d.]+)', text).group(1))
        offset_bottom = float(re.search(r'offset_bottom\s*=\s*([\-\d.]+)', text).group(1))
        w = offset_right - offset_left
        h = offset_bottom - offset_top
        assert w > 0, f"实体宽度必须 > 0，实际 {w}"
        assert h > 0, f"实体高度必须 > 0，实际 {h}"


def test_build_tscn_no_3d_objects_in_2d_scene():
    """2D 场景不应出现 MeshInstance3D / StandardMaterial3D"""
    desc = {
        "scene_name": "No3D",
        "camera": {"orthographic": True, "background_color": [0, 0, 0, 1]},
        "game_objects": [
            {"name": "P", "role": "player", "type": "CharacterBody2D",
             "position": [0, 0, 0], "color": [1, 0, 0, 1]},
        ],
    }
    tscn = GodotSceneBuilder(godot_version=4).build_tscn(desc)
    assert "MeshInstance3D" not in tscn
    assert "MeshInstance2D" not in tscn
    assert "BoxMesh" not in tscn
    assert "QuadMesh" not in tscn
