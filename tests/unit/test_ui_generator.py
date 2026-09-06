"""UIGenerator Agent + Godot UI 工具层单测（离线，不调 LLM）。

覆盖的回归点都是实现过程中真实踩到的坑：
- FULL_RECT 锚点被 DEFAULT_SIZES 污染成 280×180 的"全屏"矩形
- CENTER 预设与调用方双重偏移导致控件跑出中心
- Container 子节点残留 anchor/offset/grow（应由容器接管）
- ProgressBar 被写入 text 属性（Godot 报 Invalid property）
- HUD 根是 CanvasLayer，脚本却 extends Control
- GDM 自由文本被原样拼进 GDScript（语法错误 / 注入）
- "HUD" 被 snake_case 成 "h_u_d"
"""

import asyncio
import re

import pytest

from src.agents.ui_generator import UIGeneratorAgent
from src.agents.ui_generator.ui_tools import (
    build_ui_scene,
    guess_anchor_preset,
    resolve_root_type,
)
from src.core.tools import validate_gdscript_syntax
from src.core.tools.godot_ui_tools import (
    GodotUITools,
    UIBuilder,
    build_layout_properties,
    resolve_ui_type,
    sanitize_identifier,
    sanitize_node_name,
)

# ─── 工具层：节点工厂 ────────────────────────────────────

def test_create_button_node_shape():
    node = GodotUITools.create_button_node(name="StartButton", text="Start Game")
    assert node["type"] == "Button"
    assert node["name"] == "StartButton"
    assert node["parent"] == "."
    assert node["properties"]["text"] == "Start Game"
    assert node["properties"]["disabled"] is False


def test_create_label_node_font_size_and_align():
    node = GodotUITools.create_label_node(
        name="Title", text="Hello", font_size=32, align="center"
    )
    assert node["type"] == "Label"
    assert node["properties"]["theme_override_font_sizes/font_size"] == 32
    assert node["properties"]["horizontal_alignment"] == 1


@pytest.mark.parametrize("raw_type,expected", [
    ("Button", "Button"),      # 已是合法 Godot 类型
    ("按钮", "Button"),        # 中文别名
    ("input", "LineEdit"),     # 英文别名
    ("血条", "ProgressBar"),   # 中文别名
    ("NotAType", "Control"),   # 未知 → 默认
])
def test_resolve_ui_type_aliases(raw_type, expected):
    assert resolve_ui_type(raw_type) == expected


@pytest.mark.parametrize("name,expected", [
    ("My.Node:Name", "My_Node_Name"),
    ("", "Node"),
    ('a"b', "a_b"),
])
def test_sanitize_node_name_strips_illegal_chars(name, expected):
    assert sanitize_node_name(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("HUD", "hud"),          # 回归：HUD 曾被切成 h_u_d
    ("MainMenu", "main_menu"),
    ("HUDPanel", "hud_panel"),
    ("PauseMenu", "pause_menu"),
    ("2Player", "node_2_player"),
    ("", "node"),
])
def test_sanitize_identifier_handles_acronyms(name, expected):
    assert sanitize_identifier(name) == expected


# ─── 工具层：布局属性 ────────────────────────────────────

def test_full_rect_ignores_default_size():
    """回归：FULL_RECT 曾被 DEFAULT_SIZES 污染出 offset_right=280。"""
    props = build_layout_properties(node_type="ColorRect", anchor_preset="FULL_RECT")
    assert props["offset_left"] == 0.0
    assert props["offset_top"] == 0.0
    assert props["offset_right"] == 0.0
    assert props["offset_bottom"] == 0.0
    assert props["anchor_right"] == 1.0
    assert props["anchor_bottom"] == 1.0


def test_center_preset_centers_once():
    """回归：CENTER 的 -w/2 只能由本函数施加一次。"""
    props = build_layout_properties(
        node_type="Button", size=(200.0, 50.0), anchor_preset="CENTER"
    )
    assert props["offset_left"] == -100.0
    assert props["offset_right"] == 100.0
    assert props["offset_top"] == -25.0
    assert props["offset_bottom"] == 25.0


def test_top_left_uses_absolute_position():
    props = build_layout_properties(
        node_type="Label", position=(16.0, 16.0), size=(200.0, 28.0)
    )
    assert (props["offset_left"], props["offset_top"]) == (16.0, 16.0)
    assert (props["offset_right"], props["offset_bottom"]) == (216.0, 44.0)
    assert props["grow_horizontal"] == 1  # END


def test_top_right_grow_direction_is_begin():
    props = build_layout_properties(
        node_type="Button", position=(-96.0, 16.0), size=(80.0, 36.0),
        anchor_preset="TOP_RIGHT",
    )
    assert props["anchor_left"] == 1.0
    assert props["grow_horizontal"] == 0  # BEGIN：贴右边缘向左生长
    assert props["offset_right"] == -16.0


def test_in_container_drops_anchor_and_grow():
    """回归：Container 子节点残留 grow_horizontal=1。"""
    props = build_layout_properties(
        node_type="Button", size=(160.0, 44.0), in_container=True
    )
    assert props["layout_mode"] == 2
    assert "grow_horizontal" not in props
    assert "offset_left" not in props
    assert "anchor_left" not in props
    assert props["custom_minimum_size"] == (160.0, 44.0)
    assert props["size_flags_horizontal"] == 3


def test_non_control_type_gets_no_layout():
    assert build_layout_properties(node_type="Node2D", anchor_preset="CENTER") == {}


# ─── 工具层：UIBuilder 序列化 ────────────────────────────

def test_builder_emits_valid_tscn_header_and_load_steps():
    builder = UIBuilder("MainMenu", root_type="Control")
    builder.set_script("res://scripts/ui/main_menu.gd")
    builder.add_button(name="StartButton", text="Start")
    tscn = builder.build()

    assert tscn.startswith("[gd_scene load_steps=2 format=3")
    assert '[ext_resource type="Script" path="res://scripts/ui/main_menu.gd" id="1"]' in tscn
    assert '[node name="MainMenu" type="Control"]' in tscn
    assert 'script = ExtResource("1")' in tscn
    assert '[node name="StartButton" type="Button" parent="."]' in tscn
    assert tscn.endswith("\n")


def test_builder_load_steps_counts_sub_resources():
    builder = UIBuilder("P", root_type="Control")
    builder.add_panel(name="Panel1", background_color=(0.1, 0.2, 0.3, 1.0))
    tscn = builder.build()
    assert "[sub_resource" in tscn
    m = re.search(r"load_steps=(\d+)", tscn)
    assert m and int(m.group(1)) == 2  # 1 + 1 个 StyleBoxFlat


def test_builder_dedups_duplicate_node_names():
    builder = UIBuilder("S")
    builder.add_button(name="Btn", text="a")
    builder.add_button(name="Btn", text="b")
    names = [n["name"] for n in builder.nodes]
    assert names == ["Btn", "Btn2"]


def test_builder_container_children_get_layout_mode_2():
    builder = UIBuilder("Menu")
    builder.add_container("VBoxContainer", name="Stack")
    builder.add_button(name="A", text="A", parent="Stack")
    tscn = builder.build()

    a_node = builder.find_node("A")
    assert a_node["properties"]["layout_mode"] == 2
    assert "grow_horizontal" not in a_node["properties"]
    assert 'parent="Stack"' in tscn


def test_builder_node_path_is_nested():
    builder = UIBuilder("Menu")
    builder.add_container("VBoxContainer", name="Stack")
    builder.add_button(name="Start", text="S", parent="Stack")
    assert builder.node_path("Start") == "Stack/Start"
    assert builder.node_path("Stack") == "Stack"
    assert builder.node_path("Nope") is None


def test_builder_rejects_non_dict_node():
    builder = UIBuilder("S")
    try:
        builder.add_node("not-a-dict")
    except TypeError:
        return
    raise AssertionError("add_node 应当拒绝非 dict 输入")


# ─── Agent 层：需求抽取 ──────────────────────────────────

def test_extract_ui_requirements_covers_all_gdm_sections():
    agent = UIGeneratorAgent(config={})
    state = {
        "game_design_model": {
            "menus": [{"name": "main_menu", "elements": [{"type": "Button", "name": "B"}]}],
            "hud": {"elements": [{"type": "Label", "name": "ScoreLabel"}]},
            "dialogs": [{"name": "pause_menu", "elements": []}],
            "ui_panels": [{"name": "inventory", "elements": []}],
        }
    }
    reqs = agent._extract_ui_requirements(state)
    names = {r["name"] for r in reqs}
    assert names == {"MainMenu", "HUD", "PauseMenu", "Inventory"}
    by_name = {r["name"]: r for r in reqs}
    assert by_name["HUD"]["type"] == "hud"
    assert by_name["MainMenu"]["type"] == "menu"


def test_extract_ui_requirements_empty_without_gdm():
    agent = UIGeneratorAgent(config={})
    assert agent._extract_ui_requirements({}) == []
    assert agent._extract_ui_requirements({"game_design_model": {}}) == []


def test_extract_normalizes_loose_element_shapes():
    """GDM 可能给 list[str] / dict[name->spec] / 平铺 text，都要能吃下。"""
    agent = UIGeneratorAgent(config={})
    state = {
        "game_design_model": {
            "menus": [{
                "name": "M",
                "elements": {
                    "StartButton": {"type": "Button", "text": "Start"},
                    "QuitButton": "Button",
                },
            }],
        }
    }
    reqs = agent._extract_ui_requirements(state)
    names = {e["name"] for e in reqs[0]["elements"]}
    assert "StartButton" in names and "QuitButton" in names


def test_duplicate_ui_names_are_merged_not_overwritten():
    agent = UIGeneratorAgent(config={})
    state = {
        "game_design_model": {
            "menus": [
                {"name": "main_menu", "elements": [{"type": "Button", "name": "A"}]},
                {"name": "main_menu", "elements": [{"type": "Button", "name": "B"}]},
            ],
        }
    }
    reqs = agent._extract_ui_requirements(state)
    assert len(reqs) == 1
    assert len(reqs[0]["elements"]) == 2


# ─── Agent 层：根类型与脚本 ──────────────────────────────

def test_hud_root_is_canvas_layer_and_script_extends_it(tmp_path):
    """回归：HUD 根是 CanvasLayer，旧实现脚本硬写 extends Control。"""
    assert resolve_root_type("hud") == "CanvasLayer"
    assert resolve_root_type("menu") == "Control"
    assert resolve_root_type("dialog") == "Control"

    agent = UIGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {
            "hud": {
                "elements": [{"type": "Button", "name": "PauseButton"}],
                "interactions": [{"node": "PauseButton", "action": "toggle_pause"}],
            }
        },
    }
    result = asyncio.run(agent.execute(state))
    scene = next(iter(result["ui_scenes"].values()))

    assert scene["root_type"] == "CanvasLayer"
    assert '[node name="HUD" type="CanvasLayer"]' in scene["tscn_content"]
    assert scene["script_content"].startswith("extends CanvasLayer\n")
    # CanvasLayer 不是 Control，需要一个铺满的 Control 承载锚点
    assert '[node name="Root" type="Control" parent="."]' in scene["tscn_content"]
    # 落盘位置必须在 tmp_path 内，不能污染真实 Godot 项目根目录
    assert (tmp_path / "scenes" / "ui" / "HUD.tscn").is_file()


def test_generated_scripts_pass_project_gdscript_validator(tmp_path):
    """Tab/空格不能混用，括号必须平衡（项目自带校验器）。"""
    agent = UIGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {
            "menus": [{
                "name": "main_menu",
                "elements": [
                    {"type": "Button", "name": "StartButton", "properties": {"text": "Start"}},
                    {"type": "Button", "name": "QuitButton", "properties": {"text": "Quit"}},
                ],
                "interactions": [
                    {"node": "StartButton", "action": "change_scene",
                     "target_scene": "res://scenes/level_01.tscn"},
                    {"node": "QuitButton", "action": "quit"},
                ],
            }],
        },
    }
    result = asyncio.run(agent.execute(state))
    for scene in result["ui_scenes"].values():
        if scene.get("script_content"):
            ok, errors = validate_gdscript_syntax(scene["script_content"])
            assert ok, f"{scene['name']} 脚本未通过校验: {errors}"


def test_progress_bar_never_gets_text_property():
    """回归：GDM 的 value:80 被当成文本写成 text = "80"，ProgressBar 无此属性。"""
    builder = build_ui_scene(
        ui_name="HUD", ui_type="hud",
        elements=[{"type": "ProgressBar", "name": "HealthBar",
                   "properties": {"value": 80, "max_value": 100}}],
    )
    bar = builder.find_node("HealthBar")
    assert "text" not in bar["properties"]
    assert bar["properties"]["value"] == 80.0
    assert bar["properties"]["max_value"] == 100.0

    tscn = builder.build()
    health_block = tscn.split('[node name="HealthBar"')[1].split("[node ")[0]
    assert "text =" not in health_block


@pytest.mark.parametrize("element_name,ui_type,expected", [
    ("HealthBar", "hud", "TOP_LEFT"),
    ("ScoreLabel", "hud", "TOP_LEFT"),
    ("TimerLabel", "hud", "CENTER_TOP"),
    ("PauseButton", "hud", "TOP_RIGHT"),
    ("MinimapRect", "hud", "TOP_RIGHT"),
    # 回归：ShopButton 含子串 "hp"，曾被误判成血条丢到左上角
    ("ShopButton", "hud", "TOP_LEFT"),
    # 非 HUD 一律居中，交给容器竖排
    ("StartButton", "menu", "CENTER"),
    ("", "hud", "TOP_LEFT"),
])
def test_guess_anchor_preset_mappings(element_name, ui_type, expected):
    assert guess_anchor_preset(element_name, ui_type) == expected


def test_same_corner_elements_stack_without_overlap():
    builder = build_ui_scene(
        ui_name="HUD", ui_type="hud",
        elements=[
            {"type": "Label", "name": "ScoreLabel", "properties": {"size": [200, 28]}},
            {"type": "Label", "name": "CoinLabel", "properties": {"size": [200, 28]}},
        ],
    )
    a = builder.find_node("ScoreLabel")["properties"]
    b = builder.find_node("CoinLabel")["properties"]
    assert a["offset_top"] != b["offset_top"]
    assert b["offset_top"] > a["offset_bottom"]  # 第二个在第一个下方，不重叠


# ─── Agent 层：动作白名单 / 注入防护 ─────────────────────

def test_unknown_action_becomes_todo_not_raw_text():
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="Btn", text="B")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "Btn", "action": "make_it_rain_gold"}],
    )
    # 未知动作只允许出现在注释里，不能变成可执行语句
    executable = [ln.strip() for ln in script.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    assert not any("make_it_rain_gold" in ln for ln in executable)
    assert "pass" in script
    ok, errors = validate_gdscript_syntax(script)
    assert ok, errors


def _column_zero_lines(script: str):
    """返回脚本里所有顶格（第 0 列）的非空行。"""
    return [ln for ln in script.splitlines() if ln and not ln[0].isspace()]


def test_action_text_injection_is_blocked():
    """GDM 的自由文本不能原样进代码，也不能靠换行逃出注释。"""
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="Btn", text="B")
    payload = 'print("pwned")\n\tget_tree().quit()'
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "Btn", "action": payload}],
    )

    # 载荷必须整体被压进单行注释，换行不能把它劈成裸语句
    executable = [ln.strip() for ln in script.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    assert not any("pwned" in ln for ln in executable)

    # 顶格行只允许是 extends / class_name / func / signal 声明
    allowed = ("extends ", "class_name ", "func ", "signal ", "#", "@")
    for line in _column_zero_lines(script):
        assert line.startswith(allowed), f"顶格出现非法语句: {line!r}"

    ok, errors = validate_gdscript_syntax(script)
    assert ok, errors


def test_invalid_scene_path_is_not_emitted():
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="Btn", text="B")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "Btn", "action": "change_scene",
                       "target_scene": 'res://x.tscn") ; get_tree().quit() ; ("'}],
    )
    assert "change_scene_to_file" not in script
    assert "push_warning" in script


def test_valid_scene_path_is_emitted():
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="Btn", text="B")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "Btn", "action": "change_scene",
                       "target_scene": "res://scenes/level_01.tscn"}],
    )
    assert 'get_tree().change_scene_to_file("res://scenes/level_01.tscn")' in script


def test_interaction_referencing_missing_node_is_skipped():
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="Real", text="R")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "Ghost", "action": "quit"}],
    )
    # 没有可编译动作 → 不产出脚本，避免生成空壳 .gd
    assert script == ""


def test_callback_names_are_deduped():
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="Btn", text="B")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[
            {"node": "Btn", "action": "quit"},
            {"node": "Btn", "action": "hide"},
        ],
    )
    funcs = re.findall(r"^func (\w+)\(", script, re.MULTILINE)
    assert len(funcs) == len(set(funcs)), f"回调函数重名: {funcs}"
    ok, errors = validate_gdscript_syntax(script)
    assert ok, errors


def test_toggled_signal_gets_typed_parameter():
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_node(GodotUITools.create_check_box_node(
        name="MusicToggle", text="Music", node_type="CheckButton"))
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "MusicToggle", "action": "emit_signal",
                       "signal_name": "music_toggled"}],
    )
    assert "button_pressed: bool" in script
    assert "@onready var music_toggle: BaseButton" in script


def test_emit_signal_action_declares_the_signal():
    """回归：Godot 实测报 Parse Error "music_toggled_requested not declared"。

    ``.emit()`` 必须有配套的 ``signal`` 声明，否则整个脚本加载失败。
    Python 侧的 validate_gdscript_syntax 和 gd-guard 都查不出这类语义错误，
    所以这里显式锁定「emit 的信号必须被声明」这条不变量。
    """
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_node(GodotUITools.create_check_box_node(
        name="MusicToggle", text="Music", node_type="CheckButton"))
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "MusicToggle", "action": "emit_signal",
                       "signal_name": "music_toggled"}],
    )

    emitted = set(re.findall(r"^\s*(\w+)\.emit\(\)", script, re.MULTILINE))
    declared = set(re.findall(r"^signal (\w+)", script, re.MULTILINE))
    assert emitted, "应当生成 .emit() 调用"
    assert emitted <= declared, f"emit 了未声明的信号: {emitted - declared}"

    # 声明必须出现在 .emit() 之前
    assert script.index("signal music_toggled_requested") < script.index("music_toggled_requested.emit()")
    ok, errors = validate_gdscript_syntax(script)
    assert ok, errors


def test_repeated_emit_signal_declares_once():
    """同一信号被多个交互引用时不能重复声明（Godot 会报重复定义）。"""
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_button(name="A", text="a")
    builder.add_button(name="B", text="b")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[
            {"node": "A", "action": "emit_signal", "signal_name": "ui_action"},
            {"node": "B", "action": "emit_signal", "signal_name": "ui_action"},
        ],
    )
    assert len(re.findall(r"^signal ui_action_requested", script, re.MULTILINE)) == 1
    assert len(re.findall(r"ui_action_requested\.emit\(\)", script)) == 2
    ok, errors = validate_gdscript_syntax(script)
    assert ok, errors


def test_non_button_node_rejects_toggle_action():
    """在 Label 上生成 button_pressed 会直接编译失败。"""
    agent = UIGeneratorAgent(config={})
    builder = UIBuilder("M")
    builder.add_label(name="SomeLabel", text="hi")
    script = agent._generate_ui_script(
        ui_name="M", root_type="Control", builder=builder,
        interactions=[{"node": "SomeLabel", "signal": "pressed", "action": "toggle"}],
    )
    assert "button_pressed" not in script
    ok, errors = validate_gdscript_syntax(script)
    assert ok, errors


# ─── Agent 层：端到端落盘 ────────────────────────────────

def test_execute_writes_tscn_and_script_files(tmp_path):
    agent = UIGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {
            "menus": [{
                "name": "main_menu",
                "layout": {"title": "My Game", "background": [0.1, 0.1, 0.1, 1.0]},
                "elements": [
                    {"type": "Button", "name": "StartButton", "properties": {"text": "Start"}},
                    {"type": "Button", "name": "QuitButton", "properties": {"text": "Quit"}},
                ],
                "interactions": [
                    {"node": "StartButton", "action": "change_scene",
                     "target_scene": "res://scenes/game.tscn"},
                    {"node": "QuitButton", "action": "quit"},
                ],
            }],
            "hud": {
                "elements": [
                    {"type": "Label", "name": "ScoreLabel", "properties": {"text": "Score: 0"}},
                    {"type": "ProgressBar", "name": "HealthBar",
                     "properties": {"value": 100, "max_value": 100}},
                ],
            },
        },
    }
    result = asyncio.run(agent.execute(state))

    assert result["ui_scenes"], "应当生成 UI 场景"
    assert not result.get("error_log")

    tscn_files = list((tmp_path / "scenes" / "ui").glob("*.tscn"))
    gd_files = list((tmp_path / "scripts" / "ui").glob("*.gd"))
    assert len(tscn_files) == 2
    assert len(gd_files) == 1  # 只有 main_menu 有交互

    menu_tscn = (tmp_path / "scenes" / "ui" / "MainMenu.tscn").read_text(encoding="utf-8")
    assert "[gd_scene" in menu_tscn
    assert 'type="VBoxContainer"' in menu_tscn
    assert "Background" in menu_tscn  # layout.background 生成了 ColorRect

    menu_gd = (tmp_path / "scripts" / "ui" / "main_menu.gd").read_text(encoding="utf-8")
    assert menu_gd.startswith("extends Control")
    assert "get_tree().quit()" in menu_gd

    # code_generated 回填，便于下游 reviewer / project_generator 消费
    assert any(k.endswith(".tscn") for k in result["code_generated"])
    assert any(k.endswith(".gd") for k in result["code_generated"])


def test_execute_returns_empty_when_no_ui_requirements(tmp_path):
    agent = UIGeneratorAgent(config={})
    result = asyncio.run(agent.execute({
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {"systems": []},
    }))
    assert result == {"ui_scenes": {}}


def test_execute_is_idempotent_for_same_input(tmp_path):
    """场景 ID 由 name+type 派生，同输入同输出。"""
    agent = UIGeneratorAgent(config={})
    gdm = {"menus": [{"name": "main_menu", "elements": [
        {"type": "Button", "name": "B", "properties": {"text": "b"}}]}]}
    state = {"project_context": {"project_root": str(tmp_path)}, "game_design_model": gdm}

    first = asyncio.run(agent.execute(state))
    second = asyncio.run(agent.execute(state))
    assert list(first["ui_scenes"]) == list(second["ui_scenes"])
    assert (first["ui_scenes"][next(iter(first["ui_scenes"]))]["tscn_content"]
            == second["ui_scenes"][next(iter(second["ui_scenes"]))]["tscn_content"])


def test_config_drives_output_dirs(tmp_path):
    """agents.ui_generation.scene_dir / script_dir 可覆盖默认路径。"""
    agent = UIGeneratorAgent(config={
        "agents": {"ui_generation": {
            "scene_dir": "res://ui/scenes",
            "script_dir": "res://ui/scripts",
        }}
    })
    assert agent.scene_dir_res == "res://ui/scenes"
    assert agent.script_dir_res == "res://ui/scripts"

    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {"menus": [{
            "name": "main_menu",
            "elements": [{"type": "Button", "name": "B", "properties": {"text": "b"}}],
            "interactions": [{"node": "B", "action": "quit"}],
        }]},
    }
    result = asyncio.run(agent.execute(state))
    paths = list(result["code_generated"])
    assert any(p == "res://ui/scenes/MainMenu.tscn" for p in paths)
    assert any(p == "res://ui/scripts/main_menu.gd" for p in paths)
    assert (tmp_path / "ui" / "scenes" / "MainMenu.tscn").is_file()


def test_max_concurrent_ui_read_from_agent_section(tmp_path):
    agent = UIGeneratorAgent(config={"agents": {"ui_generation": {"max_concurrent_ui": 2}}})
    assert agent._ui_setting("max_concurrent_ui", 5) == 2
    # 未配置时回退默认值
    plain = UIGeneratorAgent(config={})
    assert plain._ui_setting("max_concurrent_ui", 5) == 5


def test_run_alias_matches_execute(tmp_path):
    """run() 是 execute() 的别名；message_bus 带时间戳，只比对确定性字段。"""
    agent = UIGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {"menus": [{"name": "m", "elements": []}]},
    }
    via_run = asyncio.run(agent.run(state))
    via_execute = asyncio.run(agent.execute(state))

    for key in ("ui_scenes", "code_generated"):
        assert via_run[key] == via_execute[key]
    assert [m["type"] for m in via_run["message_bus"]] == ["ui_generated"]


def test_build_menu_scene_helper_produces_usable_output():
    out = UIGeneratorAgent.build_menu_scene(
        "PauseMenu", ["Resume", "Quit"], title="Paused"
    )
    assert "[gd_scene" in out["tscn"]
    assert 'type="VBoxContainer"' in out["tscn"]
    # 文案推断出真实动作，而不是一堆 TODO
    assert "get_tree().paused = false" in out["script"]
    assert "get_tree().quit()" in out["script"]
    ok, errors = validate_gdscript_syntax(out["script"])
    assert ok, errors


@pytest.mark.parametrize("label,action", [
    ("Quit", "quit"),
    ("退出", "quit"),
    ("Resume", "resume"),
    ("继续", "resume"),
    ("Pause", "toggle_pause"),
    ("Back", "hide"),
    ("Start Game", "change_scene"),
    ("Something", ""),
])
def test_infer_menu_action_mapping(label, action):
    assert UIGeneratorAgent.infer_menu_action(label)["action"] == action
