"""机械验收门禁测试。"""
from src.engine.godot.scene_to_godot import default_scene_ir, write_project


def _make_project(tmp_path):
    write_project(str(tmp_path), default_scene_ir(), width=320, height=180)


def test_generated_project_passes_baseline(tmp_path):
    from src.engine.godot.baseline_checker import check_project

    _make_project(tmp_path)
    result = check_project(str(tmp_path))
    assert result["ok"], f"基线检查失败: {result['failures']}"
    assert "player_input" in result["passed"]
    assert "score_loop" in result["passed"]
    assert "sfx_assets" in result["passed"]


def test_checker_detects_missing_pieces(tmp_path):
    from src.engine.godot.baseline_checker import check_project

    _make_project(tmp_path)
    # 拆掉音效与导出预设 → 两项必须被抓住（bgm 也算音效，须整目录清掉）
    import shutil

    shutil.rmtree(tmp_path / "assets" / "sfx")
    (tmp_path / "export_presets.cfg").unlink()
    result = check_project(str(tmp_path))
    assert not result["ok"]
    failed = {f["check"] for f in result["failures"]}
    assert "sfx_assets" in failed
    assert "export_presets" in failed


def test_checker_detects_dangling_ext_resource(tmp_path):
    from src.engine.godot.baseline_checker import check_project

    _make_project(tmp_path)
    tscn = tmp_path / "scenes" / "main.tscn"
    content = tscn.read_text(encoding="utf-8")
    content = content.replace(
        '[ext_resource type="Script" path="res://addons/gameforge/runtime/game_flow.gd"',
        '[ext_resource type="Script" path="res://addons/gameforge/runtime/missing_flow.gd"',
    )
    tscn.write_text(content, encoding="utf-8")
    result = check_project(str(tmp_path))
    assert not result["ok"]
    assert any(f["check"] == "ext_resources" for f in result["failures"])
