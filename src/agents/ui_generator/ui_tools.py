"""GameForge - UI 生成 Agent 的工具层入口

真正的 Control 节点工厂与 .tscn 序列化实现在
:mod:`src.core.tools.godot_ui_tools`（与 ``godot_audio_tools`` 同层，
供 Agent / SceneBuilder / MCP 工具共用）。本模块只做两件事：

1. 重新导出 ``UIBuilder`` / ``GodotUITools``，让 Agent 内部 ``from .ui_tools import ...`` 可用；
2. 提供 UI 类型 → 布局骨架的预设，把 GDM 里松散的 ``elements`` 描述
   规整成结构合理的 Control 树（菜单居中竖排、HUD 分层锚定、对话框加遮罩）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from ...core.tools.godot_ui_tools import (
    ANCHOR_PRESETS,
    CONTAINER_TYPES,
    CONTROL_TYPES,
    DEFAULT_SIZES,
    GodotUITools,
    UIBuilder,
    build_layout_properties,
    resolve_ui_type,
    sanitize_identifier,
    sanitize_node_name,
    to_pascal_case,
)

#: 默认视口尺寸，与 project.godot 的窗口设置保持一致
DEFAULT_VIEWPORT: Sequence[float] = (1152.0, 648.0)

#: UI 类型 → 根节点类型
ROOT_TYPE_BY_UI_TYPE: Dict[str, str] = {
    "menu": "Control",
    "hud": "CanvasLayer",
    "dialog": "Control",
    "panel": "Control",
    "popup": "Control",
    "pause": "Control",
    "settings": "Control",
    "gameover": "Control",
}

#: UI 类型 → HUD 元素的锚点位置（左上/右上/左下/中上/正中）
HUD_ANCHORS: Dict[str, str] = {
    "score": "TOP_LEFT",
    "coin": "TOP_LEFT",
    "health": "TOP_LEFT",
    "hp": "TOP_LEFT",
    "life": "TOP_LEFT",
    "lives": "TOP_LEFT",
    "timer": "CENTER_TOP",
    "time": "CENTER_TOP",
    "clock": "CENTER_TOP",
    "level": "CENTER_TOP",
    "wave": "CENTER_TOP",
    "ammo": "BOTTOM_LEFT",
    "mana": "BOTTOM_LEFT",
    "energy": "BOTTOM_LEFT",
    "stamina": "BOTTOM_LEFT",
    "minimap": "TOP_RIGHT",
    "map": "TOP_RIGHT",
    "pause": "TOP_RIGHT",
    "menu_button": "TOP_RIGHT",
    "combo": "CENTER",
    "hint": "CENTER_BOTTOM",
    "message": "CENTER_BOTTOM",
    "dialogue": "CENTER_BOTTOM",
    "subtitle": "CENTER_BOTTOM",
}

#: HUD 元素的默认纵向堆叠偏移，避免多个左上角控件完全重叠
_STACK_SLOTS: Dict[str, int] = {"TOP_LEFT": 0, "BOTTOM_LEFT": 0, "TOP_RIGHT": 0, "CENTER_TOP": 0}


def resolve_root_type(ui_type: str) -> str:
    """按 UI 类型返回根节点类型（HUD 用 CanvasLayer，其余用 Control）。"""
    return ROOT_TYPE_BY_UI_TYPE.get((ui_type or "").lower(), "Control")


def guess_anchor_preset(element_name: str, ui_type: str = "hud") -> str:
    """按元素名猜测锚点预设。

    HUD 元素优先按语义（score/health/timer/minimap）分配角落；
    菜单/对话框元素一律居中竖排，交给容器布局。

    匹配按 ``_`` 分词做前缀比对，避免 ``ShopButton`` 因为含子串 "hp"
    被误判成血条而丢到左上角。
    """
    if (ui_type or "").lower() != "hud":
        return "CENTER"
    tokens = [t for t in sanitize_identifier(element_name or "").lower().split("_") if t]
    if not tokens:
        return "TOP_LEFT"
    for token, preset in HUD_ANCHORS.items():
        if token in tokens or any(t.startswith(token) for t in tokens):
            return preset
    return "TOP_LEFT"


def build_ui_scene(
    ui_name: str,
    ui_type: str,
    elements: Optional[List[Dict[str, Any]]] = None,
    layout: Optional[Dict[str, Any]] = None,
    script_path: Optional[str] = None,
    viewport: Sequence[float] = DEFAULT_VIEWPORT,
) -> UIBuilder:
    """把 GDM 的 UI 需求构建成 :class:`UIBuilder`。

    Args:
        ui_name: UI 名称（MainMenu / HUD / PauseMenu ...）
        ui_type: menu / hud / dialog / panel
        elements: 子元素列表，每项 ``{type, name, properties}``
        layout: 布局信息，支持 ``anchor`` / ``size`` / ``background`` / ``title``
        script_path: 交互脚本的 res:// 路径，传入则挂到根节点
        viewport: 视口尺寸，用于 HUD 的角落锚定计算

    Returns:
        已填充节点的 UIBuilder，调用 ``.build()`` 得到 .tscn 文本
    """
    elements = list(elements or [])
    layout = dict(layout or {})
    ui_type_l = (ui_type or "panel").lower()
    root_type = resolve_root_type(ui_type_l)

    builder = UIBuilder(scene_name=ui_name, root_type=root_type)
    if script_path:
        builder.set_script(script_path)

    if root_type == "CanvasLayer":
        # CanvasLayer 不是 Control，需要一个铺满屏幕的 Control 承载锚点布局
        builder.set_root_properties({"layer": int(layout.get("layer", 10))})
        builder.add_node(GodotUITools.create_control_node(
            node_type="Control",
            name="Root",
            parent=".",
            anchor_preset="FULL_RECT",
            properties={"mouse_filter": 2},  # IGNORE，HUD 不应拦截游戏输入
        ))
        parent = "Root"
    else:
        builder.set_root_properties(_root_control_properties(ui_type_l, layout))
        parent = "."

    # 背景色 → 全屏 ColorRect（必须在内容之前，保证绘制顺序在下层）
    background = _extract_background(layout)
    if background:
        _add_background_rect(builder, parent, background)

    # 对话框/暂停菜单：再铺一层半透明遮罩
    if ui_type_l in ("dialog", "pause", "gameover"):
        builder.add_node(GodotUITools.create_fullscreen_dimmer(parent=parent))

    # 标题（layout.title 或菜单名）
    title = layout.get("title") or (ui_name if ui_type_l == "menu" else None)
    if title and ui_type_l in ("menu", "dialog", "pause", "settings", "gameover"):
        builder.add_node(GodotUITools.create_label_node(
            name="TitleLabel",
            text=str(title),
            parent=parent,
            anchor_preset="CENTER_TOP",
            position=(0.0, 64.0),
            size=(480.0, 56.0),
            font_size=int(layout.get("title_font_size", 40)),
            align="center",
        ))

    if ui_type_l == "hud":
        _add_hud_elements(builder, elements, parent, viewport)
    else:
        _add_stacked_elements(builder, elements, parent, ui_type_l, layout)

    return builder


def _root_control_properties(ui_type: str, layout: Dict[str, Any]) -> Dict[str, Any]:
    """根 Control 的属性：是否拦截鼠标。

    背景色不在这里处理——它需要一个 ColorRect 子节点，见
    :func:`_add_background_rect`，直接塞进根属性会写出非法的 .tscn 字段。
    """
    # 菜单/对话框需要吃掉点击，避免穿透到游戏世界
    return {"mouse_filter": 0 if ui_type in ("menu", "dialog", "pause", "gameover") else 1}


def _extract_background(layout: Dict[str, Any]) -> Optional[Sequence[float]]:
    """从 layout 中取出背景色 RGBA，支持 list / dict / Color(...) 字符串。"""
    raw = layout.get("background") or layout.get("background_color")
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        vals = [float(v) for v in list(raw)[:3]]
        vals.append(float(raw[3]) if len(raw) > 3 else 1.0)
        return tuple(vals)
    if isinstance(raw, dict):
        return (
            float(raw.get("r", 0.0)),
            float(raw.get("g", 0.0)),
            float(raw.get("b", 0.0)),
            float(raw.get("a", 1.0)),
        )
    if isinstance(raw, str):
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", raw)]
        if len(nums) >= 3:
            nums = nums[:3] + ([nums[3]] if len(nums) > 3 else [1.0])
            return tuple(nums)
    return None


def _add_background_rect(builder: UIBuilder, parent: str, color: Sequence[float]) -> None:
    """添加铺满父容器的背景 ColorRect。"""
    builder.add_node(GodotUITools.create_control_node(
        node_type="ColorRect",
        name="Background",
        parent=parent,
        anchor_preset="FULL_RECT",
        properties={"color": tuple(color), "mouse_filter": 2},
        metadata={"ui_role": "background"},
    ))


def _add_hud_elements(
    builder: UIBuilder,
    elements: List[Dict[str, Any]],
    parent: str,
    viewport: Sequence[float],
) -> None:
    """HUD：按语义锚定到四个角落/边缘，同锚点内纵向堆叠避免重叠。

    ``viewport`` 仅用于记录来源信息；位置全部用 anchor 相对坐标表达，
    这样窗口缩放时 HUD 会自动跟随边缘。
    """
    slots: Dict[str, int] = {key: 0 for key in _STACK_SLOTS}
    margin = 16.0

    for elem in elements:
        elem_type = resolve_ui_type(elem.get("type", "Label"), default="Label")
        elem_name = sanitize_node_name(
            elem.get("name") or elem_type, fallback=elem_type
        )
        props = dict(elem.get("properties") or {})
        preset = guess_anchor_preset(elem_name, "hud")

        w, h = _as_pair_safe(
            props.get("size"), DEFAULT_SIZES.get(elem_type, (200.0, 32.0))
        )
        slot = slots.get(preset, 0)
        slots[preset] = slot + 1

        builder.add_node(GodotUITools.create_control_node(
            node_type=elem_type,
            name=elem_name,
            parent=parent,
            position=_anchor_position(preset, slot, w, h, margin),
            size=(w, h),
            anchor_preset=preset,
            properties=_element_properties(elem_type, props),
            metadata={
                "ui_role": elem.get("role"),
                "anchor": preset,
                "source": "gdm",
                "viewport": list(viewport),
            },
        ))


def _anchor_position(preset: str, slot: int, w: float, h: float, margin: float) -> Sequence[float]:
    """按锚点预设和堆叠序号算出 offset 用的 (x, y)。

    返回值是「相对锚点」的坐标：``build_layout_properties`` 会再按
    anchor==0.5 的轴自动补上 -尺寸/2 的居中偏移，所以这里不能重复偏移。
    同理，锚点在 1.0（贴右/贴下）时用负值把控件拉回可视区。
    """
    gap = 12.0
    step = h + gap

    if preset == "TOP_LEFT":
        return (margin, margin + slot * step)
    if preset == "TOP_RIGHT":
        return (-(margin + w), margin + slot * step)
    if preset == "BOTTOM_LEFT":
        return (margin, -(margin + h) - slot * step)
    if preset == "BOTTOM_RIGHT":
        return (-(margin + w), -(margin + h) - slot * step)
    if preset == "CENTER_TOP":
        return (0.0, margin + slot * step)
    if preset == "CENTER_BOTTOM":
        return (0.0, -(margin + h))
    if preset == "CENTER_LEFT":
        return (margin, 0.0)
    if preset == "CENTER_RIGHT":
        return (-(margin + w), 0.0)
    if preset == "CENTER":
        return (0.0, 0.0)
    return (margin, margin)


def _add_stacked_elements(
    builder: UIBuilder,
    elements: List[Dict[str, Any]],
    parent: str,
    ui_type: str,
    layout: Dict[str, Any],
) -> None:
    """菜单/对话框：用一个居中容器竖排所有元素。"""
    if not elements:
        return

    container_name = sanitize_node_name(
        layout.get("container_name") or f"{to_pascal_case(ui_type)}Stack",
        fallback="Stack",
    )
    container = GodotUITools.create_container_node(
        container_type="VBoxContainer",
        name=container_name,
        parent=parent,
        anchor_preset=layout.get("anchor", "CENTER"),
        size=_as_pair_safe(layout.get("size"), (280.0, 360.0)),
        separation=int(layout.get("separation", 12)),
    )
    builder.add_node(container)
    container_name = container["name"]  # add_node 可能对重名做了去重

    for elem in elements:
        elem_type = resolve_ui_type(elem.get("type", "Button"), default="Button")
        elem_name = sanitize_node_name(
            elem.get("name") or elem_type, fallback=elem_type
        )
        props = dict(elem.get("properties") or {})
        builder.add_node(GodotUITools.create_control_node(
            node_type=elem_type,
            name=elem_name,
            parent=container_name,
            size=props.get("size"),
            in_container=True,
            properties=_element_properties(elem_type, props),
            metadata={"ui_role": elem.get("role"), "source": "gdm"},
        ))


def _as_pair_safe(value: Any, default: Sequence[float]) -> Sequence[float]:
    """_as_pair 的容错包装，避免 layout 里给了非法 size 时抛异常。"""
    from ...core.tools.godot_ui_tools import _as_pair

    try:
        return _as_pair(value, default)
    except Exception:  # noqa: BLE001
        return tuple(default)


#: 真正拥有 text 属性的控件；ProgressBar/Slider/Container 等写了会报 Invalid property
TEXT_CAPABLE_TYPES: frozenset = frozenset({
    "Button", "TextureButton", "LinkButton", "CheckButton", "CheckBox",
    "Label", "LineEdit", "TextEdit", "CodeEdit", "RichTextLabel", "OptionButton",
})

#: 拥有 disabled 属性的控件
DISABLEABLE_TYPES: frozenset = frozenset({
    "Button", "TextureButton", "LinkButton", "CheckButton", "CheckBox",
    "LineEdit", "TextEdit", "CodeEdit", "OptionButton", "SpinBox",
    "HSlider", "VSlider",
})


def _element_properties(elem_type: str, props: Dict[str, Any]) -> Dict[str, Any]:
    """把 GDM 元素属性翻译成 Godot Control 属性。

    只写目标节点类型真实拥有的属性：例如 GDM 里 ProgressBar 的 ``value: 80``
    必须落到 ``value``，不能被当成文本写成 ``text = "80"``（ProgressBar 没有
    text 属性，Godot 加载场景会直接报 Invalid property）。
    """
    out: Dict[str, Any] = {}

    # 文本：仅对确实有 text 属性的控件生效
    if elem_type in TEXT_CAPABLE_TYPES:
        for key in ("text", "label", "title", "content", "caption"):
            if props.get(key) is not None:
                out["text"] = str(props[key])
                break

    if elem_type == "Label":
        if props.get("font_size"):
            out["theme_override_font_sizes/font_size"] = int(props["font_size"])
        if props.get("align"):
            align_map = {"left": 0, "center": 1, "right": 2, "fill": 3}
            align = str(props["align"]).lower()
            if align in align_map:
                out["horizontal_alignment"] = align_map[align]
        if props.get("autowrap"):
            out["autowrap_mode"] = 2
    elif elem_type == "LineEdit":
        if props.get("placeholder"):
            out["placeholder_text"] = str(props["placeholder"])
        if props.get("max_length"):
            out["max_length"] = int(props["max_length"])
        if props.get("secret"):
            out["secret"] = True
    elif elem_type in ("TextEdit", "CodeEdit"):
        if props.get("placeholder"):
            out["placeholder_text"] = str(props["placeholder"])
        if props.get("readonly"):
            out["editable"] = False
    elif elem_type == "ProgressBar":
        for key in ("min_value", "max_value", "value"):
            if props.get(key) is not None:
                out[key] = _to_float(props[key], 0.0)
        out.setdefault("min_value", 0.0)
        out.setdefault("max_value", 100.0)
        out.setdefault("value", 0.0)
        out["show_percentage"] = bool(props.get("show_percentage", False))
    elif elem_type in ("HSlider", "VSlider"):
        for key in ("min_value", "max_value", "value", "step"):
            if props.get(key) is not None:
                out[key] = _to_float(props[key], 0.0)
    elif elem_type == "SpinBox":
        for key in ("min_value", "max_value", "value", "step"):
            if props.get(key) is not None:
                out[key] = _to_float(props[key], 0.0)
    elif elem_type in ("CheckBox", "CheckButton"):
        if props.get("pressed") is not None:
            out["button_pressed"] = bool(props["pressed"])
    elif elem_type == "OptionButton":
        items = props.get("items") or props.get("options") or []
        items = list(items)
        for idx, item in enumerate(items):
            out[f"popup/item_{idx}/text"] = str(item)
        if items:
            out["item_count"] = len(items)
    elif elem_type == "ColorRect":
        color = _extract_background(props)
        if color:
            out["color"] = tuple(color)

    # 所有 Control 通用属性
    for key in ("visible", "modulate", "tooltip_text", "mouse_filter"):
        if key in props:
            out[key] = props[key]
    # disabled 只有部分控件有
    if "disabled" in props and elem_type in DISABLEABLE_TYPES:
        out["disabled"] = bool(props["disabled"])

    return out


def _to_float(value: Any, default: float) -> float:
    """容错转 float，GDM 里给了 "80" 这种字符串时不至于抛异常。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "ANCHOR_PRESETS",
    "CONTROL_TYPES",
    "CONTAINER_TYPES",
    "DEFAULT_SIZES",
    "DEFAULT_VIEWPORT",
    "HUD_ANCHORS",
    "ROOT_TYPE_BY_UI_TYPE",
    "GodotUITools",
    "UIBuilder",
    "build_layout_properties",
    "build_ui_scene",
    "guess_anchor_preset",
    "resolve_root_type",
    "resolve_ui_type",
    "sanitize_identifier",
    "sanitize_node_name",
    "to_pascal_case",
]
