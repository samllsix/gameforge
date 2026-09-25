"""GameForge - Godot UI 工具

封装 Godot Control 体系节点（Button / Label / Panel / LineEdit / TextEdit /
ProgressBar / 各类 Container 等）的创建，并把节点描述序列化为 .tscn 文本。

分层设计（与 godot_audio_tools 保持一致）：
- ``GodotUITools``: 纯工厂层，每个 ``create_*_node`` 返回「节点描述 dict」，
  可直接喂给 ``UIBuilder``，也可交给 ``GodotSceneBuilder`` 复用。
- ``UIBuilder``: 序列化层，把节点描述按 Godot 4 文本场景格式拼成 ``.tscn``，
  自动维护 load_steps / ext_resource / parent 路径。

节点描述 dict 结构::

    {
        "name": "StartButton",
        "type": "Button",
        "parent": ".",            # "." 表示根节点的直接子节点
        "properties": {...},       # 直接写入 .tscn 的属性
        "metadata": {...},         # 仅供 Python 侧使用，不写入 .tscn
    }

Godot 4 布局约定：Control 的位置/尺寸由 anchor_* + offset_* 决定，
``position=(x, y)`` / ``size=(w, h)`` 会被换算成对应的 offset_*。
父节点是 Container 时 offset_* 被容器接管，因此改走 layout_mode=2。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ...utils.logger import get_logger

logger = get_logger(__name__)


# ─── 常量 ───────────────────────────────────────────────

#: Control 的 LayoutPreset 枚举值（Godot 4 Control.LayoutPreset）
ANCHOR_PRESETS: Dict[str, Dict[str, float]] = {
    "TOP_LEFT": {"anchor_left": 0.0, "anchor_top": 0.0, "anchor_right": 0.0, "anchor_bottom": 0.0},
    "TOP_RIGHT": {"anchor_left": 1.0, "anchor_top": 0.0, "anchor_right": 1.0, "anchor_bottom": 0.0},
    "BOTTOM_LEFT": {"anchor_left": 0.0, "anchor_top": 1.0, "anchor_right": 0.0, "anchor_bottom": 1.0},
    "BOTTOM_RIGHT": {"anchor_left": 1.0, "anchor_top": 1.0, "anchor_right": 1.0, "anchor_bottom": 1.0},
    "CENTER_LEFT": {"anchor_left": 0.0, "anchor_top": 0.5, "anchor_right": 0.0, "anchor_bottom": 0.5},
    "CENTER_TOP": {"anchor_left": 0.5, "anchor_top": 0.0, "anchor_right": 0.5, "anchor_bottom": 0.0},
    "CENTER_RIGHT": {"anchor_left": 1.0, "anchor_top": 0.5, "anchor_right": 1.0, "anchor_bottom": 0.5},
    "CENTER_BOTTOM": {"anchor_left": 0.5, "anchor_top": 1.0, "anchor_right": 0.5, "anchor_bottom": 1.0},
    "CENTER": {"anchor_left": 0.5, "anchor_top": 0.5, "anchor_right": 0.5, "anchor_bottom": 0.5},
    "FULL_RECT": {"anchor_left": 0.0, "anchor_top": 0.0, "anchor_right": 1.0, "anchor_bottom": 1.0},
}

#: 所有 Control 派生节点类型（用于判定是否需要写 anchor/offset）
CONTROL_TYPES: frozenset = frozenset({
    "Control", "Container", "BoxContainer", "HBoxContainer", "VBoxContainer",
    "GridContainer", "FlowContainer", "MarginContainer", "TabContainer",
    "PanelContainer", "ScrollContainer", "SplitContainer", "CenterContainer",
    "AspectRatioContainer", "Label", "Button", "LineEdit", "TextEdit",
    "CodeEdit", "RichTextLabel", "TextureRect", "NinePatchRect", "ColorRect",
    "Panel", "ProgressBar", "HSlider", "VSlider", "HScrollBar", "VScrollBar",
    "SpinBox", "CheckBox", "CheckButton", "OptionButton", "MenuButton",
    "LinkButton", "TextureButton", "AcceptDialog", "ConfirmationDialog",
    "Window", "Popup", "PopupMenu", "Tree", "ItemList", "TabBar",
    "VideoStreamPlayer", "GraphEdit", "GraphNode", "SubViewportContainer",
})

#: 会接管子节点布局的 Container 类型
CONTAINER_TYPES: frozenset = frozenset({
    "Container", "BoxContainer", "HBoxContainer", "VBoxContainer",
    "GridContainer", "FlowContainer", "MarginContainer", "TabContainer",
    "PanelContainer", "ScrollContainer", "SplitContainer", "CenterContainer",
    "AspectRatioContainer", "SubViewportContainer",
})

#: 默认尺寸（宽, 高），按控件类型给出可用初值
DEFAULT_SIZES: Dict[str, Tuple[float, float]] = {
    "Button": (160.0, 44.0),
    "Label": (200.0, 28.0),
    "LineEdit": (220.0, 36.0),
    "TextEdit": (320.0, 120.0),
    "RichTextLabel": (320.0, 120.0),
    "Panel": (280.0, 180.0),
    "ColorRect": (280.0, 180.0),
    "TextureRect": (128.0, 128.0),
    "ProgressBar": (220.0, 24.0),
    "HSlider": (200.0, 24.0),
    "VSlider": (24.0, 200.0),
    "CheckBox": (160.0, 32.0),
    "CheckButton": (160.0, 36.0),
    "OptionButton": (180.0, 36.0),
    "SpinBox": (140.0, 36.0),
    "TextureButton": (96.0, 96.0),
    "LinkButton": (160.0, 28.0),
    "ItemList": (240.0, 180.0),
    "Tree": (320.0, 200.0),
}

#: 未知控件类型的兜底尺寸（宽, 高）
FALLBACK_SIZE: Tuple[float, float] = (100.0, 40.0)

#: Godot 节点名最大长度（超过部分截断）
MAX_NODE_NAME_LENGTH = 64

#: Panel 默认圆角半径（StyleBoxFlat corner_radius_*）
PANEL_CORNER_RADIUS = 4

#: GDM 里可能出现的别名 → Godot 节点类型
UI_TYPE_ALIASES: Dict[str, str] = {
    "button": "Button",
    "btn": "Button",
    "按钮": "Button",
    "label": "Label",
    "text": "Label",
    "title": "Label",
    "标签": "Label",
    "文本": "Label",
    "标题": "Label",
    "panel": "Panel",
    "面板": "Panel",
    "lineedit": "LineEdit",
    "line_edit": "LineEdit",
    "input": "LineEdit",
    "inputfield": "LineEdit",
    "输入框": "LineEdit",
    "textedit": "TextEdit",
    "text_edit": "TextEdit",
    "textarea": "TextEdit",
    "richtextlabel": "RichTextLabel",
    "richtext": "RichTextLabel",
    "progressbar": "ProgressBar",
    "progress": "ProgressBar",
    "healthbar": "ProgressBar",
    "血条": "ProgressBar",
    "进度条": "ProgressBar",
    "checkbox": "CheckBox",
    "checkbutton": "CheckButton",
    "toggle": "CheckButton",
    "optionbutton": "OptionButton",
    "dropdown": "OptionButton",
    "select": "OptionButton",
    "下拉框": "OptionButton",
    "spinbox": "SpinBox",
    "slider": "HSlider",
    "hs": "HSlider",
    "hsilder": "HSlider",
    "vs": "VSlider",
    "texturebutton": "TextureButton",
    "linkbutton": "LinkButton",
    "itemlist": "ItemList",
    "tree": "Tree",
    "control": "Control",
    "container": "VBoxContainer",
    "hbox": "HBoxContainer",
    "hboxcontainer": "HBoxContainer",
    "vbox": "VBoxContainer",
    "vboxcontainer": "VBoxContainer",
    "grid": "GridContainer",
    "gridcontainer": "GridContainer",
    "margin": "MarginContainer",
    "margincontainer": "MarginContainer",
    "center": "CenterContainer",
    "centercontainer": "CenterContainer",
    "panelcontainer": "PanelContainer",
    "scroll": "ScrollContainer",
    "scrollcontainer": "ScrollContainer",
    "colorrect": "ColorRect",
    "tabcontainer": "TabContainer",
}

_UNQUOTED_PREFIXES = (
    "Color(", "Vector2(", "Vector2i(", "Vector3(", "Vector3i(",
    "Rect2(", "Rect2i(", "Transform2D(", "Transform3D(",
    "SubResource(", "ExtResource(", "NodePath(",
)

_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


# ─── 基础工具 ────────────────────────────────────────────

def sanitize_node_name(name: str, fallback: str = "Node") -> str:
    """把任意字符串规整为合法的 Godot 节点名。

    Godot 节点名不能包含 ``.`` ``:`` ``@`` ``/`` ``"`` ``%``，
    这里统一替换为下划线，并去掉首尾空白。
    """
    if not name:
        return fallback
    cleaned = re.sub(r'[.:@/"%]', "_", str(name)).strip()
    cleaned = cleaned.strip("_") or fallback
    return cleaned[:MAX_NODE_NAME_LENGTH]


def sanitize_identifier(name: str, prefix: str = "node") -> str:
    """规整为合法的 GDScript 标识符（snake_case），用于回调函数名、文件名等。

    正确处理连续大写缩写：``HUD`` → ``hud``（不是 ``h_u_d``），
    ``HUDPanel`` → ``hud_panel``，``MainMenu`` → ``main_menu``。
    """
    if not name:
        return prefix
    s = str(name)
    # 小写/数字 → 大写 之间断词（MainMenu → Main_Menu）
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    # 缩写 → 新单词 之间断词（HUDPanel → HUD_Panel）
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    s = _IDENT_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    if not s:
        return prefix
    if s[0].isdigit():
        s = f"{prefix}_{s}"
    return s


def to_pascal_case(name: str, fallback: str = "UI") -> str:
    """转 PascalCase，用于 class_name。"""
    if not name:
        return fallback
    parts = re.split(r"[^A-Za-z0-9]+", str(name))
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return out or fallback


def resolve_ui_type(raw_type: str, default: str = "Control") -> str:
    """把 GDM 里的宽松类型名解析为 Godot Control 节点类型。

    已是合法 Godot 类型时原样返回；命中别名表时返回映射值；
    都不匹配则退回 ``default``。
    """
    if not raw_type:
        return default
    text = str(raw_type).strip()
    if text in CONTROL_TYPES:
        return text
    mapped = UI_TYPE_ALIASES.get(text.lower())
    if mapped:
        return mapped
    logger.warning(f"未知 UI 类型 {raw_type!r}，回退为 {default}")
    return default


def format_tscn_value(value: Any) -> str:
    """把 Python 值序列化为 .tscn 属性字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Godot 对 float 属性接受 0 / 1.0 两种写法，统一保留一位小数更稳
        return repr(value) if value != int(value) else f"{value:.1f}"
    if isinstance(value, str):
        if value.startswith(_UNQUOTED_PREFIXES):
            return value
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        nums = [_as_float(v) for v in value]
        if len(nums) == 2:
            return f"Vector2({nums[0]}, {nums[1]})"
        if len(nums) == 3:
            return f"Vector3({nums[0]}, {nums[1]}, {nums[2]})"
        if len(nums) == 4:
            return f"Color({nums[0]}, {nums[1]}, {nums[2]}, {nums[3]})"
        return "[" + ", ".join(format_tscn_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{}"
    return str(value)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_pair(value: Any, default: Sequence[float]) -> Tuple[float, float]:
    """把 position/size 归一化为 (x, y) 浮点二元组。"""
    if value is None:
        return float(default[0]), float(default[1])
    if isinstance(value, (list, tuple)):
        if len(value) >= 2:
            return _as_float(value[0]), _as_float(value[1])
        if len(value) == 1:
            return _as_float(value[0]), float(default[1])
    if isinstance(value, dict):
        return (
            _as_float(value.get("x", default[0])),
            _as_float(value.get("y", default[1])),
        )
    if isinstance(value, str):
        text = value.strip().strip("()[]")
        parts = [p for p in re.split(r"[,\s]+", text) if p]
        if len(parts) >= 2:
            return _as_float(parts[0]), _as_float(parts[1])
    return float(default[0]), float(default[1])


# ─── 布局属性 ────────────────────────────────────────────

def _grow_direction(anchor_start: float, anchor_end: float) -> int:
    """Godot 4 GrowDirection：0=BEGIN, 1=END, 2=BOTH。

    锚点塌缩到一个点时，生长方向由该点落在哪条边决定；
    锚点跨满整轴（如 FULL_RECT）时双向生长。
    """
    if anchor_start != anchor_end:
        return 2  # BOTH：跨满整轴
    if anchor_start >= 1.0:
        return 0  # BEGIN：贴远边，向起始方向生长
    if anchor_start <= 0.0:
        return 1  # END：贴近边，向末尾方向生长
    return 2      # BOTH：居中，双向生长


def build_layout_properties(
    position: Any = None,
    size: Any = None,
    node_type: str = "Control",
    anchor_preset: Optional[str] = None,
    in_container: bool = False,
    default_size: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """生成 Control 节点的 anchor_* / offset_* 布局属性。

    Args:
        position: 相对锚点的位置 (x, y)。TOP_LEFT 预设下即父节点内绝对坐标；
            CENTER_* 预设下留 0 即可，函数会自动把尺寸对半偏移实现居中。
        size: 尺寸 (w, h)
        node_type: Godot 节点类型；非 Control 派生则返回空 dict
        anchor_preset: ANCHOR_PRESETS 的键名，如 "CENTER" / "FULL_RECT"
        in_container: 父节点是否为 Container；是则由容器接管布局
        default_size: size 缺省时的兜底尺寸
    """
    if node_type not in CONTROL_TYPES:
        return {}

    preset = ANCHOR_PRESETS.get((anchor_preset or "TOP_LEFT").upper())
    if preset is None:
        logger.warning(f"未知 anchor_preset {anchor_preset!r}，使用 TOP_LEFT")
        preset = ANCHOR_PRESETS["TOP_LEFT"]

    fallback = default_size or DEFAULT_SIZES.get(node_type, FALLBACK_SIZE)
    w, h = _as_pair(size, fallback)
    x, y = _as_pair(position, (0.0, 0.0))

    props: Dict[str, Any] = dict(preset)
    props["grow_horizontal"] = _grow_direction(preset["anchor_left"], preset["anchor_right"])
    props["grow_vertical"] = _grow_direction(preset["anchor_top"], preset["anchor_bottom"])

    if in_container:
        # Container 内部：anchor/offset/grow 全部由容器接管，写了反而是噪声
        props = {"layout_mode": 2}
        if node_type not in CONTAINER_TYPES:
            props["custom_minimum_size"] = (w, h)
            # SIZE_FILL：菜单按钮在 VBox 里横向铺满，符合常见菜单观感
            props["size_flags_horizontal"] = 3
        return props

    props["layout_mode"] = 0

    # 锚点跨满整轴（如 FULL_RECT）时该轴尺寸由父节点决定，必须忽略传入的 size，
    # 否则会用 DEFAULT_SIZES 兜底出一个 280×180 的"全屏"矩形。
    full_x = preset["anchor_left"] == 0.0 and preset["anchor_right"] == 1.0
    full_y = preset["anchor_top"] == 0.0 and preset["anchor_bottom"] == 1.0

    if full_x:
        props["offset_left"] = x          # 显式 position 时当作左内边距
        props["offset_right"] = -x if x else 0.0
    else:
        center_x = -w / 2 if preset["anchor_left"] == 0.5 else 0.0
        props["offset_left"] = x + center_x
        props["offset_right"] = x + w + center_x

    if full_y:
        props["offset_top"] = y
        props["offset_bottom"] = -y if y else 0.0
    else:
        center_y = -h / 2 if preset["anchor_top"] == 0.5 else 0.0
        props["offset_top"] = y + center_y
        props["offset_bottom"] = y + h + center_y

    return props


# ─── 工厂层 ──────────────────────────────────────────────

class GodotUITools:
    """Godot Control 节点工厂

    所有 ``create_*_node`` 都返回统一的「节点描述 dict」，不直接拼字符串，
    方便上层做校验、复用或改写后再序列化。
    """

    # ── 通用 ──

    @staticmethod
    def create_control_node(
        node_type: str,
        name: str,
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        in_container: bool = False,
        properties: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建任意 Control 派生节点的描述。

        Args:
            node_type: Godot 节点类型（Button/Label/...）
            name: 节点名
            parent: 父节点路径，"." 表示根节点的直接子节点
            position: 位置 (x, y)
            size: 尺寸 (w, h)，缺省时用 DEFAULT_SIZES
            anchor_preset: 锚点预设名
            in_container: 父节点是否为 Container
            properties: 额外属性，会覆盖自动生成的布局属性
            metadata: Python 侧元数据，不写入 .tscn
        """
        resolved_type = resolve_ui_type(node_type, default=node_type or "Control")
        node_name = sanitize_node_name(name, fallback=resolved_type)

        props: Dict[str, Any] = build_layout_properties(
            position=position,
            size=size,
            node_type=resolved_type,
            anchor_preset=anchor_preset,
            in_container=in_container,
        )
        props.update(properties or {})

        return {
            "name": node_name,
            "type": resolved_type,
            "parent": parent,
            "properties": props,
            "metadata": dict(metadata or {}),
        }

    # ── 常用控件 ──

    @staticmethod
    def create_button_node(
        name: str = "Button",
        text: str = "Button",
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        disabled: bool = False,
        flat: bool = False,
        toggle_mode: bool = False,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 Button 节点描述。"""
        return GodotUITools.create_control_node(
            node_type="Button",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties={
                "text": text or "Button",
                "disabled": bool(disabled),
                "flat": bool(flat),
                "toggle_mode": bool(toggle_mode),
                **(extra_properties or {}),
            },
            metadata={"ui_role": "button", "text": text or "Button"},
        )

    @staticmethod
    def create_label_node(
        name: str = "Label",
        text: str = "Label",
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        font_size: Optional[int] = None,
        align: str = "left",
        autowrap: bool = False,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 Label 节点描述。

        Args:
            align: left / center / right（映射到 Godot 4 HorizontalAlignment）
            font_size: 字号，写入 theme_override_font_sizes/font_size
            autowrap: 是否自动换行
        """
        align_map = {"left": 0, "center": 1, "right": 2, "fill": 3}
        props: Dict[str, Any] = {"text": text or ""}
        if autowrap:
            # Godot 4: AutowrapMode 1 = WORD, 2 = WORD_SMART
            props["autowrap_mode"] = 2
        if align in align_map:
            props["horizontal_alignment"] = align_map[align]
        if font_size:
            props["theme_override_font_sizes/font_size"] = int(font_size)
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type="Label",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "label", "text": text or ""},
        )

    @staticmethod
    def create_panel_node(
        name: str = "Panel",
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        background_color: Optional[Sequence[float]] = None,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 Panel 节点描述。

        传入 background_color 时会附带一个 StyleBoxFlat 子资源引用，
        由 UIBuilder 负责把子资源展开成 [sub_resource] 段。
        """
        props: Dict[str, Any] = {}
        if background_color:
            r, g, b = (_as_float(v) for v in list(background_color)[:3])
            a = _as_float(background_color[3]) if len(background_color) > 3 else 1.0
            props["theme_override_styles/panel"] = {
                "__sub_resource__": "StyleBoxFlat",
                "bg_color": (r, g, b, a),
                "corner_radius_top_left": PANEL_CORNER_RADIUS,
                "corner_radius_top_right": PANEL_CORNER_RADIUS,
                "corner_radius_bottom_right": PANEL_CORNER_RADIUS,
                "corner_radius_bottom_left": PANEL_CORNER_RADIUS,
            }
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type="Panel",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "panel"},
        )

    @staticmethod
    def create_line_edit_node(
        name: str = "LineEdit",
        placeholder: str = "",
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        max_length: Optional[int] = None,
        secret: bool = False,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 LineEdit（单行输入框）节点描述。"""
        props: Dict[str, Any] = {
            "placeholder_text": placeholder or "",
            "secret": bool(secret),
        }
        if max_length:
            props["max_length"] = int(max_length)
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type="LineEdit",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "input", "placeholder": placeholder or ""},
        )

    @staticmethod
    def create_text_edit_node(
        name: str = "TextEdit",
        placeholder: str = "",
        text: str = "",
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        readonly: bool = False,
        wrap: bool = True,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 TextEdit（多行文本框）节点描述。"""
        props: Dict[str, Any] = {
            "placeholder_text": placeholder or "",
            "text": text or "",
            "editable": not readonly,
            "wrap_mode": 1 if wrap else 0,
        }
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type="TextEdit",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "textarea"},
        )

    @staticmethod
    def create_progress_bar_node(
        name: str = "ProgressBar",
        value: float = 0.0,
        min_value: float = 0.0,
        max_value: float = 100.0,
        show_percentage: bool = False,
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 ProgressBar 节点描述（血条 / 进度条）。"""
        props: Dict[str, Any] = {
            "min_value": _as_float(min_value),
            "max_value": _as_float(max_value),
            "value": _as_float(value),
            "show_percentage": bool(show_percentage),
        }
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type="ProgressBar",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "progress"},
        )

    @staticmethod
    def create_check_box_node(
        name: str = "CheckBox",
        text: str = "",
        pressed: bool = False,
        node_type: str = "CheckBox",
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 CheckBox / CheckButton 节点描述。"""
        resolved = node_type if node_type in ("CheckBox", "CheckButton") else "CheckBox"
        props: Dict[str, Any] = {"text": text or "", "button_pressed": bool(pressed)}
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type=resolved,
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "toggle"},
        )

    @staticmethod
    def create_option_button_node(
        name: str = "OptionButton",
        items: Optional[Iterable[str]] = None,
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 OptionButton（下拉框）节点描述。

        Godot 的 OptionButton 选项写在 ``popup/item_N/text`` 上，
        这里按索引展开，保证 .tscn 加载后有可选项。
        """
        props: Dict[str, Any] = {}
        for idx, item in enumerate(items or []):
            props[f"popup/item_{idx}/text"] = str(item)
        if items:
            props["item_count"] = len(list(items))
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type="OptionButton",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "dropdown"},
        )

    @staticmethod
    def create_container_node(
        container_type: str = "VBoxContainer",
        name: Optional[str] = None,
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        separation: Optional[int] = None,
        in_container: bool = False,
        extra_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建 Container 节点描述（HBox/VBox/Grid/Margin/Center/Panel 等）。"""
        resolved = resolve_ui_type(container_type, default="VBoxContainer")
        if resolved not in CONTAINER_TYPES:
            logger.warning(f"{resolved} 不是 Container，回退为 VBoxContainer")
            resolved = "VBoxContainer"

        props: Dict[str, Any] = {}
        if separation is not None and resolved in (
            "HBoxContainer", "VBoxContainer", "BoxContainer", "GridContainer", "FlowContainer"
        ):
            props["theme_override_constants/separation"] = int(separation)
        if resolved == "GridContainer":
            props.setdefault("columns", 2)
        if resolved == "MarginContainer":
            for side in ("left", "top", "right", "bottom"):
                props.setdefault(f"theme_override_constants/margin_{side}", 16)
        props.update(extra_properties or {})

        return GodotUITools.create_control_node(
            node_type=resolved,
            name=name or resolved,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset or ("FULL_RECT" if resolved == "MarginContainer" else None),
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "container"},
        )

    @staticmethod
    def create_texture_rect_node(
        name: str = "TextureRect",
        texture_path: Optional[str] = None,
        parent: str = ".",
        position: Any = None,
        size: Any = None,
        anchor_preset: Optional[str] = None,
        stretch_mode: int = 5,
        in_container: bool = False,
    ) -> Dict[str, Any]:
        """创建 TextureRect 节点描述。

        Args:
            texture_path: res:// 贴图路径，缺省时不写 texture 属性
            stretch_mode: Godot 4 StretchMode，5 = KEEP_ASPECT_CENTERED
        """
        props: Dict[str, Any] = {"stretch_mode": int(stretch_mode)}
        if texture_path:
            props["texture"] = f'ExtResource("{texture_path}")'

        return GodotUITools.create_control_node(
            node_type="TextureRect",
            name=name,
            parent=parent,
            position=position,
            size=size,
            anchor_preset=anchor_preset,
            in_container=in_container,
            properties=props,
            metadata={"ui_role": "image", "texture": texture_path},
        )

    # ── 常用组合 ──

    @staticmethod
    def create_fullscreen_dimmer(
        name: str = "Dimmer",
        color: Sequence[float] = (0.0, 0.0, 0.0, 0.6),
        parent: str = ".",
    ) -> Dict[str, Any]:
        """创建全屏半透明遮罩（暂停菜单/对话框常用）。"""
        r, g, b = (_as_float(v) for v in list(color)[:3])
        a = _as_float(color[3]) if len(color) > 3 else 0.6
        return GodotUITools.create_control_node(
            node_type="ColorRect",
            name=name,
            parent=parent,
            anchor_preset="FULL_RECT",
            properties={
                "color": (r, g, b, a),
                "mouse_filter": 0,  # STOP，拦截点击
            },
            metadata={"ui_role": "dimmer"},
        )

    @staticmethod
    def create_menu_stack(
        name: str = "MenuStack",
        separation: int = 12,
        anchor_preset: str = "CENTER",
        size: Sequence[float] = (240.0, 320.0),
    ) -> Dict[str, Any]:
        """创建居中竖直菜单容器（主菜单/暂停菜单的标准骨架）。"""
        return GodotUITools.create_container_node(
            container_type="VBoxContainer",
            name=name,
            anchor_preset=anchor_preset,
            size=size,
            separation=separation,
        )

    @staticmethod
    def build_hud_nodes(
        score_label: str = "ScoreLabel",
        health_bar: str = "HealthBar",
        viewport: Sequence[float] = (1152.0, 648.0),
    ) -> List[Dict[str, Any]]:
        """生成一套标准 HUD 节点：左上分数、左下血条、右上暂停按钮。"""
        vw, vh = _as_pair(viewport, (1152.0, 648.0))
        return [
            GodotUITools.create_label_node(
                name=score_label,
                text="Score: 0",
                position=(16.0, 16.0),
                size=(240.0, 32.0),
                font_size=24,
                anchor_preset="TOP_LEFT",
            ),
            GodotUITools.create_progress_bar_node(
                name=health_bar,
                value=100.0,
                max_value=100.0,
                position=(16.0, vh - 40.0),
                size=(220.0, 20.0),
                anchor_preset="TOP_LEFT",
            ),
            GodotUITools.create_button_node(
                name="PauseButton",
                text="Pause",
                position=(-96.0, 16.0),
                size=(80.0, 36.0),
                anchor_preset="TOP_RIGHT",
            ),
        ]


# ─── 序列化层 ────────────────────────────────────────────

class UIBuilder:
    """Godot UI 场景构建器

    把节点描述 dict 序列化为 Godot 4 ``.tscn`` 文本。

    典型用法::

        builder = UIBuilder("MainMenu")
        builder.set_root("Control")
        builder.set_script("res://scripts/ui/main_menu.gd")
        builder.add_label(name="Title", text="My Game", anchor_preset="CENTER_TOP")
        builder.add_button(name="StartButton", text="Start", position=[0, 120])
        tscn = builder.build()
    """

    def __init__(
        self,
        scene_name: str = "UI",
        root_type: str = "Control",
        script_path: Optional[str] = None,
        godot_version: int = 4,
    ):
        self.scene_name = sanitize_node_name(scene_name, fallback="UI")
        self.root_type = root_type if root_type in ("Control", "CanvasLayer", "Window", "Popup") else "Control"
        self.script_path = script_path
        self.godot_version = godot_version
        self._nodes: List[Dict[str, Any]] = []
        self._sub_resources: List[Dict[str, Any]] = []
        self._sub_dedup: Dict[str, str] = {}
        self._root_properties: Dict[str, Any] = {}
        self._used_names: set = set()

    # ── 根节点配置 ──

    def set_root(self, root_type: str, **properties: Any) -> "UIBuilder":
        """设置根节点类型（Control / CanvasLayer / Window）。"""
        if root_type in ("Control", "CanvasLayer", "Window", "Popup"):
            self.root_type = root_type
        else:
            logger.warning(f"UI 根节点类型 {root_type!r} 非法，保持 {self.root_type}")
        self._root_properties.update(properties or {})
        return self

    def set_root_properties(self, properties: Dict[str, Any]) -> "UIBuilder":
        """批量设置根节点属性。"""
        self._root_properties.update(properties or {})
        return self

    def set_script(self, script_path: Optional[str]) -> "UIBuilder":
        """挂载 GDScript 到根节点（写入 .tscn 的 ext_resource）。"""
        self.script_path = script_path or None
        return self

    # ── 子资源 ──

    def _add_sub_resource(self, res_type: str, properties: Dict[str, Any]) -> str:
        """登记子资源并返回 ``SubResource("id")`` 引用，相同内容自动去重。"""
        import json

        key = f"{res_type}:{json.dumps(properties, sort_keys=True, default=str)}"
        if key in self._sub_dedup:
            return self._sub_dedup[key]
        sub_id = f"ui_sub_{len(self._sub_resources) + 1}"
        self._sub_resources.append({"type": res_type, "id": sub_id, "properties": properties})
        ref = f'SubResource("{sub_id}")'
        self._sub_dedup[key] = ref
        return ref

    # ── 添加节点 ──

    def add_node(self, node_desc: Dict[str, Any]) -> Dict[str, Any]:
        """添加一个节点描述（工厂层产出），返回归一化后的描述。"""
        if not isinstance(node_desc, dict):
            raise TypeError(f"node_desc 必须是 dict，收到 {type(node_desc).__name__}")

        node_type = node_desc.get("type") or "Control"
        name = sanitize_node_name(node_desc.get("name", ""), fallback=node_type)
        name = self._unique_name(name)

        parent = node_desc.get("parent", ".") or "."
        props = dict(node_desc.get("properties") or {})

        # Container 的子节点由容器接管布局：清掉手写 anchor/offset/grow，改 layout_mode=2
        parent_type = self._parent_type(parent)
        if parent_type in CONTAINER_TYPES:
            props = dict(props)
            props["layout_mode"] = 2
            for key in ("offset_left", "offset_top", "offset_right", "offset_bottom",
                        "anchor_left", "anchor_top", "anchor_right", "anchor_bottom",
                        "grow_horizontal", "grow_vertical"):
                props.pop(key, None)
            if node_type not in CONTAINER_TYPES:
                w, h = _as_pair(node_desc.get("size"), DEFAULT_SIZES.get(node_type, FALLBACK_SIZE))
                props.setdefault("custom_minimum_size", (w, h))
                props.setdefault("size_flags_horizontal", 3)

        normalized = {
            "name": name,
            "type": node_type,
            "parent": parent,
            "properties": props,
            "metadata": dict(node_desc.get("metadata") or {}),
        }
        self._nodes.append(normalized)
        return normalized

    def _parent_type(self, parent: str) -> str:
        """按 parent 路径反查父节点类型；"." 表示根节点。"""
        if parent in (".", ""):
            return self.root_type
        head = parent.split("/")[0]
        for node in self._nodes:
            if node["name"] == head:
                return node["type"]
        return self.root_type

    def _unique_name(self, name: str) -> str:
        """节点名去重，重名时追加序号。"""
        if name not in self._used_names:
            self._used_names.add(name)
            return name
        idx = 2
        while f"{name}{idx}" in self._used_names:
            idx += 1
        unique = f"{name}{idx}"
        self._used_names.add(unique)
        logger.warning(f"UI 节点名重复：{name} → {unique}")
        return unique

    # ── 便捷 add_* 接口（与工厂层同名，自动登记到当前 builder）──

    def add_button(self, name: Optional[str] = None, node_name: Optional[str] = None,
                   text: str = "Button", parent: str = ".", position: Any = None,
                   size: Any = None, anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 Button。"""
        return self.add_node(GodotUITools.create_button_node(
            name=name or node_name or "Button", text=text, parent=parent,
            position=position, size=size, anchor_preset=anchor_preset, **kwargs,
        ))

    def add_label(self, name: Optional[str] = None, node_name: Optional[str] = None,
                  text: str = "Label", parent: str = ".", position: Any = None,
                  size: Any = None, anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 Label。"""
        return self.add_node(GodotUITools.create_label_node(
            name=name or node_name or "Label", text=text, parent=parent,
            position=position, size=size, anchor_preset=anchor_preset, **kwargs,
        ))

    def add_panel(self, name: Optional[str] = None, node_name: Optional[str] = None,
                  parent: str = ".", position: Any = None, size: Any = None,
                  anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 Panel。"""
        return self.add_node(GodotUITools.create_panel_node(
            name=name or node_name or "Panel", parent=parent, position=position,
            size=size, anchor_preset=anchor_preset, **kwargs,
        ))

    def add_line_edit(self, name: Optional[str] = None, node_name: Optional[str] = None,
                      placeholder: str = "", parent: str = ".", position: Any = None,
                      size: Any = None, anchor_preset: Optional[str] = None,
                      **kwargs: Any) -> Dict[str, Any]:
        """添加 LineEdit。"""
        return self.add_node(GodotUITools.create_line_edit_node(
            name=name or node_name or "LineEdit", placeholder=placeholder, parent=parent,
            position=position, size=size, anchor_preset=anchor_preset, **kwargs,
        ))

    def add_text_edit(self, name: Optional[str] = None, node_name: Optional[str] = None,
                      placeholder: str = "", text: str = "", parent: str = ".",
                      position: Any = None, size: Any = None,
                      anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 TextEdit。"""
        return self.add_node(GodotUITools.create_text_edit_node(
            name=name or node_name or "TextEdit", placeholder=placeholder, text=text,
            parent=parent, position=position, size=size, anchor_preset=anchor_preset,
            **kwargs,
        ))

    def add_progress_bar(self, name: Optional[str] = None, node_name: Optional[str] = None,
                         parent: str = ".", position: Any = None, size: Any = None,
                         anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 ProgressBar。"""
        return self.add_node(GodotUITools.create_progress_bar_node(
            name=name or node_name or "ProgressBar", parent=parent, position=position,
            size=size, anchor_preset=anchor_preset, **kwargs,
        ))

    def add_container(self, container_type: str = "VBoxContainer", name: Optional[str] = None,
                      parent: str = ".", position: Any = None, size: Any = None,
                      anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 Container（返回的描述里 name 可作为后续子节点的 parent）。"""
        return self.add_node(GodotUITools.create_container_node(
            container_type=container_type, name=name, parent=parent, position=position,
            size=size, anchor_preset=anchor_preset, **kwargs,
        ))

    def add_texture_rect(self, name: Optional[str] = None, node_name: Optional[str] = None,
                         texture_path: Optional[str] = None, parent: str = ".",
                         position: Any = None, size: Any = None,
                         anchor_preset: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """添加 TextureRect。"""
        return self.add_node(GodotUITools.create_texture_rect_node(
            name=name or node_name or "TextureRect", texture_path=texture_path,
            parent=parent, position=position, size=size, anchor_preset=anchor_preset,
            **kwargs,
        ))

    def add_nodes(self, node_descs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量添加节点描述。"""
        return [self.add_node(desc) for desc in node_descs or []]

    # ── 内省 ──

    @property
    def nodes(self) -> List[Dict[str, Any]]:
        """当前已登记的节点描述（只读快照）。"""
        return [dict(n) for n in self._nodes]

    def find_node(self, name: str) -> Optional[Dict[str, Any]]:
        """按名字查找节点描述。"""
        for node in self._nodes:
            if node["name"] == name:
                return dict(node)
        return None

    def node_path(self, name: str) -> Optional[str]:
        """返回节点相对根节点的 NodePath，如 ``MenuStack/StartButton``。"""
        target = self.find_node(name)
        if not target:
            return None
        parent = target.get("parent", ".")
        if parent in (".", ""):
            return target["name"]
        return f"{parent}/{target['name']}"

    def interactive_nodes(self) -> List[Dict[str, Any]]:
        """返回可交互节点（Button / CheckButton / LineEdit / OptionButton 等）。"""
        interactive = {
            "Button", "TextureButton", "LinkButton", "CheckButton", "CheckBox",
            "OptionButton", "MenuButton", "LineEdit", "TextEdit", "SpinBox",
            "HSlider", "VSlider", "ItemList",
        }
        return [dict(n) for n in self._nodes if n["type"] in interactive]

    # ── 序列化 ──

    def build(self) -> str:
        """生成 .tscn 文本内容。"""
        ext_resources: List[Dict[str, str]] = []
        script_ref: Optional[str] = None

        if self.script_path:
            res_id = str(len(ext_resources) + 1)
            ext_resources.append({"type": "Script", "path": self.script_path, "id": res_id})
            script_ref = f'ExtResource("{res_id}")'

        # 先渲染节点，过程中可能登记子资源
        node_lines: List[str] = []
        node_lines.extend(self._render_root(script_ref))
        for node in self._nodes:
            node_lines.extend(self._render_node(node))

        load_steps = 1 + len(ext_resources) + len(self._sub_resources)
        lines = [f"[gd_scene load_steps={load_steps} format=3 uid=\"uid://gf{self._uid_seed()}\"]", ""]

        for res in ext_resources:
            lines.append(f'[ext_resource type="{res["type"]}" path="{res["path"]}" id="{res["id"]}"]')
        if ext_resources:
            lines.append("")

        for sub in self._sub_resources:
            lines.append(f'[sub_resource type="{sub["type"]}" id="{sub["id"]}"]')
            for key, value in sub["properties"].items():
                lines.append(f"{key} = {format_tscn_value(value)}")
            lines.append("")

        lines.extend(node_lines)
        text = "\n".join(lines)
        return text if text.endswith("\n") else text + "\n"

    def _uid_seed(self) -> str:
        """由场景名派生稳定的 uid 片段（同输入同输出，便于 diff）。"""
        import hashlib

        digest = hashlib.md5(f"gf-ui:{self.scene_name}".encode()).hexdigest()
        # Godot uid 使用小写字母数字，取前 10 位足够避免常见碰撞
        return digest[:10]

    def _render_root(self, script_ref: Optional[str]) -> List[str]:
        lines = [f'[node name="{self.scene_name}" type="{self.root_type}"]']

        if self.root_type == "Control":
            # 菜单/面板默认铺满父视口，避免 0×0 不可见
            props = dict(self._root_properties)
            props.setdefault("anchor_right", 1.0)
            props.setdefault("anchor_bottom", 1.0)
            props.setdefault("grow_horizontal", 2)
            props.setdefault("grow_vertical", 2)
            props.setdefault("mouse_filter", 1)  # PASS，不阻断下层输入
        else:
            props = dict(self._root_properties)

        if self.root_type == "CanvasLayer":
            props.setdefault("layer", 10)

        if script_ref:
            lines.append(f"script = {script_ref}")

        for key, value in props.items():
            lines.append(f"{key} = {format_tscn_value(value)}")

        lines.append("")
        return lines

    def _render_node(self, node: Dict[str, Any]) -> List[str]:
        parent = node.get("parent", ".") or "."
        header = f'[node name="{node["name"]}" type="{node["type"]}" parent="{parent}"]'
        lines = [header]

        for key, value in (node.get("properties") or {}).items():
            if isinstance(value, dict) and "__sub_resource__" in value:
                res_type = value["__sub_resource__"]
                res_props = {k: v for k, v in value.items() if k != "__sub_resource__"}
                lines.append(f"{key} = {self._add_sub_resource(res_type, res_props)}")
            else:
                lines.append(f"{key} = {format_tscn_value(value)}")

        lines.append("")
        return lines


__all__ = [
    "ANCHOR_PRESETS",
    "CONTROL_TYPES",
    "CONTAINER_TYPES",
    "DEFAULT_SIZES",
    "FALLBACK_SIZE",
    "MAX_NODE_NAME_LENGTH",
    "PANEL_CORNER_RADIUS",
    "UI_TYPE_ALIASES",
    "GodotUITools",
    "UIBuilder",
    "build_layout_properties",
    "format_tscn_value",
    "resolve_ui_type",
    "sanitize_identifier",
    "sanitize_node_name",
    "to_pascal_case",
]
