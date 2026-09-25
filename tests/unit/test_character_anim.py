"""角色序列帧（walk/run cycle）生成单元测试。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image

from src.engine.godot.character_anim import (
    ANIM_PROMPTS,
    FRAME_EDGE,
    _procedural_cycle,
    _slice_horizontal_strip,
    build_player_animations,
)


def test_anim_prompt_specs_cover_mvp():
    assert set(ANIM_PROMPTS) >= {"idle", "run", "jump"}
    assert ANIM_PROMPTS["run"]["frames"] == 4
    assert "run cycle" in ANIM_PROMPTS["run"]["prompt"]
    assert "side view" in ANIM_PROMPTS["run"]["prompt"]


def test_slice_horizontal_strip_equal_width():
    img = Image.new("RGBA", (400, 100), (255, 0, 0, 255))
    # 画四条不同色便于断言
    for i, c in enumerate([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]):
        for x in range(i * 100, (i + 1) * 100):
            for y in range(100):
                img.putpixel((x, y), c)
    frames = _slice_horizontal_strip(img, 4)
    assert len(frames) == 4
    assert frames[0].size[0] == 100
    assert frames[1].getpixel((10, 10))[:3] == (0, 255, 0)


def test_procedural_cycle_produces_frames(tmp_path: Path):
    base = tmp_path / "player.png"
    Image.new("RGBA", (64, 64), (120, 220, 255, 255)).save(base)
    frames = _procedural_cycle(str(base), 4, "run")
    assert len(frames) == 4
    assert all(f.mode == "RGBA" for f in frames)


def test_build_player_animations_procedural(tmp_path: Path):
    project = tmp_path / "proj"
    gen = project / "assets" / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (96, 96), (200, 100, 50, 255)).save(gen / "player.png")

    # 强制程序化，不打 AI
    result = build_player_animations(
        str(project),
        prefer_ai=False,
        force=True,
    )
    assert result["ok"] is True
    assert result["source"] == "procedural"
    for anim in ("idle", "run", "jump"):
        assert anim in result["anims"]
        frames = result["anims"][anim]["frames"]
        assert len(frames) == ANIM_PROMPTS[anim]["frames"]
        for fr in frames:
            path = project / "assets" / "gen" / "player" / Path(fr["path"]).name
            assert path.is_file()
    meta = project / "assets" / "gen" / "player_anim.json"
    assert meta.is_file()
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["ok"] is True


def test_build_scene_tscn_emits_animated_sprite(tmp_path: Path):
    from src.agents.scene_ir import CameraIR, EntityIR, SceneIR
    from src.engine.godot.scene_to_godot import build_scene_tscn

    project = tmp_path / "proj"
    gen = project / "assets" / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 64), (10, 200, 120, 255)).save(gen / "player.png")
    anim = build_player_animations(str(project), prefer_ai=False, force=True)

    ir = SceneIR(
        scene_name="GameScene",
        genre="platformer",
        layout="linear",
        difficulty="medium",
        camera=CameraIR(),
        entities=[EntityIR(name="Player", role="player"), EntityIR(name="Ground", role="ground")],
    )
    tscn = build_scene_tscn(ir, player_anim=anim)
    assert "AnimatedSprite2D" in tscn
    assert 'SpriteFrames' in tscn
    assert '&"run"' in tscn
    assert '&"idle"' in tscn
    assert "player/run_0.png" in tscn or "run_0.png" in tscn
