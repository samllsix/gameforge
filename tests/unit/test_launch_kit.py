"""上线闭环组件测试：音效合成 / 导出预设 / 场景上线件。"""
import os

from src.engine.godot.scene_to_godot import build_scene_tscn, default_scene_ir, write_project


# ── sfx_forge ────────────────────────────────────────────────────────────────

def test_write_sfx_creates_wavs(tmp_path):
    from src.engine.godot.sfx_forge import write_sfx

    written = write_sfx(str(tmp_path))
    assert set(written) == {"jump", "coin", "death", "click", "bgm"}
    import wave

    for name in written:
        path = os.path.join(tmp_path, written[name])
        with wave.open(path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 22050
            assert wf.getnframes() > 100


def test_write_sfx_idempotent(tmp_path):
    from src.engine.godot.sfx_forge import write_sfx

    write_sfx(str(tmp_path))
    before = os.listdir(tmp_path)
    write_sfx(str(tmp_path))  # 已存在则跳过
    assert os.listdir(tmp_path) == before


# ── export_kit：预设文件 ─────────────────────────────────────────────────────

def test_write_export_presets(tmp_path):
    from src.engine.godot.export_kit import write_export_presets

    out = tmp_path / "export_presets.cfg"
    out.unlink(missing_ok=True)  # tmp_path 跨 pytest 运行复用，先清理
    content = open(write_export_presets(str(tmp_path)), encoding="utf-8").read()
    assert 'name="Web"' in content and 'platform="Web"' in content
    assert 'name="Windows Desktop"' in content
    assert 'export_path="export/web/index.html"' in content
    # 幂等：已有配置不覆盖
    open(out, "w", encoding="utf-8").write("# custom")
    assert write_export_presets(str(tmp_path)).endswith("export_presets.cfg")
    assert open(out, encoding="utf-8").read() == "# custom"


# ── 场景上线件 ───────────────────────────────────────────────────────────────

def test_tscn_contains_launch_components():
    tscn = build_scene_tscn(default_scene_ir(), width=320, height=180)
    # 游戏流程层与三个面板
    assert '[node name="GameFlow" type="CanvasLayer" parent="." groups=["game_flow"]]' in tscn
    assert '[node name="BootPanel" type="ColorRect" parent="GameFlow"]' in tscn
    assert '[node name="PausePanel" type="ColorRect" parent="GameFlow"]' in tscn
    assert '[node name="OverPanel" type="ColorRect" parent="GameFlow"]' in tscn
    assert "GAME OVER" in tscn and "重新开始 (R)" in tscn
    # 玩家/敌人分组（死亡判定依赖）
    assert 'groups=["player"]' in tscn
    assert 'groups=["enemy"]' in tscn
    # 按钮信号连接
    assert 'method="_on_restart_pressed"' in tscn
    assert 'method="_on_quit_pressed"' in tscn
    # 像素风文本在 UI 中
    assert "点击开始游戏" in tscn


def test_write_project_emits_launch_files(tmp_path):
    write_project(str(tmp_path), default_scene_ir(), width=320, height=180)
    # 音效 / 导出预设 / 图标 / 游戏流程脚本
    assert os.path.isfile(tmp_path / "assets" / "sfx" / "jump.wav")
    assert os.path.isfile(tmp_path / "export_presets.cfg")
    assert os.path.isfile(tmp_path / "icon.png")
    assert os.path.isfile(tmp_path / "addons" / "gameforge" / "runtime" / "game_flow.gd")
    pg = (tmp_path / "project.godot").read_text(encoding="utf-8")
    assert 'config/icon="res://icon.png"' in pg
