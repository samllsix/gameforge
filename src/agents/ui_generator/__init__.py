"""GameForge - UI 生成 Agent

根据 GDM 中的 UI 需求，生成 Godot UI 场景（.tscn）和交互脚本（.gd）。

结构对齐 :mod:`src.agents.audio_generator`：

1. 从 GDM 抽取 UI 需求（menus / hud / dialogs / ui_panels）
2. 交给 UI 工具层（:mod:`src.core.tools.godot_ui_tools`）构建 Control 节点树
3. 序列化为 ``res://scenes/ui/*.tscn``
4. 有交互需求时生成 ``res://scripts/ui/*.gd`` 并挂到场景根节点
5. 更新 ``state.ui_scenes``

设计原则（与 audio_engine / asset_forge 一致）：
- 失败开放：单个 UI 生成失败不影响其余 UI，异常只写入 error_log
- 幂等：场景 ID 由 name+type 派生，同输入同输出，便于 diff 与增量重生成
- 强约束：GDScript 一律 Tab 缩进 + 2.0 类型注解；动作/信号走白名单，
  GDM 里的自由文本不会被原样拼进代码（避免语法错误与注入）
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.gdm import as_item
from ...core.state.game_state import AgentType, GameDevState
from ...utils.logger import get_logger
from ..base import BaseAgent
from .ui_tools import (
    CONTROL_TYPES,
    GodotUITools,
    UIBuilder,
    build_ui_scene,
    resolve_root_type,
    sanitize_identifier,
    sanitize_node_name,
    to_pascal_case,
)

logger = get_logger(__name__)


#: Godot 4 Control 体系里可安全 connect 的信号白名单
_ALLOWED_SIGNALS: frozenset = frozenset({
    "pressed", "button_up", "button_down", "toggled",
    "text_changed", "text_submitted", "value_changed",
    "item_selected", "focus_entered", "focus_exited",
    "gui_input", "visibility_changed", "mouse_entered", "mouse_exited",
    "confirmed", "canceled", "cancelled", "about_to_popup",
})

#: 拥有 button_pressed / grab_focus 的按钮类节点
_BUTTON_TYPES: frozenset = frozenset({
    "Button", "TextureButton", "LinkButton", "CheckButton", "CheckBox", "OptionButton",
})

#: 合法 res:// 场景路径，用于拦截 change_scene 目标里的任意代码
_SCENE_PATH_RE = re.compile(r"^res://[A-Za-z0-9_\-./]+\.tscn$")

#: 节点类型 → 该类型上最常见的可连接信号
_DEFAULT_SIGNAL_BY_TYPE: Dict[str, str] = {
    "Button": "pressed",
    "TextureButton": "pressed",
    "LinkButton": "pressed",
    "CheckButton": "toggled",
    "CheckBox": "toggled",
    "LineEdit": "text_submitted",
    "TextEdit": "text_changed",
    "OptionButton": "item_selected",
    "HSlider": "value_changed",
    "VSlider": "value_changed",
    "ProgressBar": "value_changed",
    "SpinBox": "value_changed",
    "ItemList": "item_selected",
}

#: 节点类型 → GDScript 强类型声明
_TYPED_DECL_BY_NODE: Dict[str, str] = {
    "Button": "Button", "TextureButton": "BaseButton", "LinkButton": "BaseButton",
    "CheckButton": "BaseButton", "CheckBox": "CheckBox", "Label": "Label",
    "LineEdit": "LineEdit", "TextEdit": "TextEdit", "RichTextLabel": "RichTextLabel",
    "OptionButton": "OptionButton", "ProgressBar": "ProgressBar",
    "HSlider": "Slider", "VSlider": "Slider", "SpinBox": "SpinBox",
    "Panel": "Panel", "ColorRect": "ColorRect", "TextureRect": "TextureRect",
    "ItemList": "ItemList", "Control": "Control", "CanvasLayer": "CanvasLayer",
    "VBoxContainer": "VBoxContainer", "HBoxContainer": "HBoxContainer",
    "GridContainer": "GridContainer", "MarginContainer": "MarginContainer",
    "CenterContainer": "CenterContainer", "PanelContainer": "PanelContainer",
    "ScrollContainer": "ScrollContainer", "Container": "Container",
}

TAB = "\t"

#: 并发生成 UI 场景的默认上限（agents.ui_generation.max_concurrent_ui 缺省值）
DEFAULT_MAX_CONCURRENT_UI = 5

#: 场景 ID（md5 摘要）保留长度，与 audio_generator 的资产 ID 保持一致
OBJECT_ID_LENGTH = 16


class UIGeneratorAgent(BaseAgent):
    """UI 生成 Agent

    职责:
    1. 从 GDM 中提取 UI 需求（菜单、HUD、对话框等）
    2. 调用 UI 工具层生成 Control 节点树
    3. 写入 res://scenes/ui/ 目录的 .tscn 文件
    4. 生成 UI 交互脚本（按钮点击、输入处理等）并挂到场景根节点
    5. 更新 state.ui_scenes
    """

    #: 生成的 .tscn 在 Godot 项目里的目录
    SCENE_DIR = "res://scenes/ui"
    #: 生成的 .gd 在 Godot 项目里的目录
    SCRIPT_DIR = "res://scripts/ui"

    def __init__(self, llm_client: Any = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            agent_type=AgentType.CODE_GENERATOR,  # 复用 code_generator 枚举，保持 6-Agent 契约
            config=config or {},
        )
        # llm_client 当前不参与生成（UI 结构由 GDM 确定性推导），保留形参以对齐调用方
        self.llm_client = llm_client

        # 专属配置段：agents.ui_generation；缺省时回退到顶层同名键，保持向后兼容
        self.ui_config: Dict[str, Any] = dict(
            (config or {}).get("agents", {}).get("ui_generation", {}) or {}
        )
        self.scene_dir_res = str(
            self.ui_config.get("scene_dir", self.SCENE_DIR)
        ).rstrip("/")
        self.script_dir_res = str(
            self.ui_config.get("script_dir", self.SCRIPT_DIR)
        ).rstrip("/")
        self.output_dir = Path(self.scene_dir_res.replace("res://", ""))
        self.script_dir = Path(self.script_dir_res.replace("res://", ""))

    def _ui_setting(self, key: str, default: Any) -> Any:
        """读取 UI 生成配置：优先 agents.ui_generation，回退顶层 config。"""
        if key in self.ui_config:
            return self.ui_config[key]
        return self.config.get(key, default)

    # ─── 对外入口 ───────────────────────────────────────

    async def execute(self, state: GameDevState, **kwargs: Any) -> Dict[str, Any]:
        """执行 UI 生成。

        Args:
            state: 当前游戏开发状态

        Returns:
            状态增量：``ui_scenes`` / ``code_generated`` / ``message_bus`` / ``error_log``
        """
        logger.info("UIGeneratorAgent 开始执行")

        try:
            # 1. 提取 UI 需求
            ui_requirements = self._extract_ui_requirements(state)
            if not ui_requirements:
                logger.warning("GDM 中未找到 UI 需求,跳过生成")
                return {"ui_scenes": {}}

            logger.info(f"提取到 {len(ui_requirements)} 个 UI 需求")

            # 2. 生成 UI 场景和脚本
            ui_scenes = await self._generate_ui_scenes(ui_requirements)

            # 3. 写入文件
            await self._write_ui_files(ui_scenes, state)

            # 4. 返回更新
            #    code_generated 一并回填，让 code_reviewer / project_generator
            #    这些按「生成文件字典」工作的下游节点能看到 UI 脚本
            code_generated: Dict[str, str] = {}
            for scene in ui_scenes:
                code_generated[scene["file_path"]] = scene["tscn_content"]
                if scene.get("script_content"):
                    code_generated[scene["script_path"]] = scene["script_content"]

            return {
                "ui_scenes": {scene["id"]: scene for scene in ui_scenes},
                "code_generated": code_generated,
                "message_bus": [
                    {
                        "type": "ui_generated",
                        "agent": "ui_generator",
                        "timestamp": datetime.now().isoformat(),
                        "data": {
                            "count": len(ui_scenes),
                            "names": [s["name"] for s in ui_scenes],
                        },
                    }
                ],
            }

        except Exception as e:  # noqa: BLE001
            logger.error(f"UI 生成失败: {e}", exc_info=True)
            return {
                "error_log": [f"UIGenerator error: {str(e)}"],
                "ui_scenes": {},
            }

    async def run(self, state: GameDevState, **kwargs: Any) -> Dict[str, Any]:
        """AudioGenerator 风格的别名入口，便于按同一约定调用。"""
        return await self.execute(state, **kwargs)

    # ─── 需求抽取 ───────────────────────────────────────

    def _extract_ui_requirements(self, state: GameDevState) -> List[Dict[str, Any]]:
        """从 GDM 中提取 UI 需求。

        Args:
            state: 游戏开发状态

        Returns:
            UI 需求列表，每项包含:
            - name: UI 名称（MainMenu / HUD / PauseMenu ...）
            - type: UI 类型（menu / hud / dialog / panel）
            - elements: 子元素列表（按钮、标签、输入框等）
            - layout: 布局信息（anchor / size / background / title）
            - interactions: 交互逻辑（按钮点击事件等）
        """
        gdm = state.get("game_design_model")
        if not gdm:
            return []

        requirements: List[Dict[str, Any]] = []

        # 菜单（主菜单 / 暂停菜单 / 设置菜单）
        for menu in gdm.get("menus") or []:
            requirements.append(self._normalize_requirement(menu, "menu"))

        # HUD（单个对象，不是列表）
        hud = gdm.get("hud")
        if isinstance(hud, dict):
            requirements.append(self._normalize_requirement(hud, "hud", forced_name="HUD"))
        elif isinstance(hud, list):
            for item in hud:
                requirements.append(self._normalize_requirement(item, "hud"))

        # 对话框
        for dialog in gdm.get("dialogs") or []:
            requirements.append(self._normalize_requirement(dialog, "dialog"))

        # 通用 UI 面板
        for panel in gdm.get("ui_panels") or []:
            requirements.append(self._normalize_requirement(panel, "panel"))

        # 兼容 GDM 里直接给 ui / interfaces 字段
        for item in gdm.get("ui") or gdm.get("interfaces") or []:
            if isinstance(item, dict):
                requirements.append(
                    self._normalize_requirement(item, item.get("type") or "panel")
                )

        # 名称去重：同名 UI 会写同一个文件，后者覆盖前者
        seen: Dict[str, Dict[str, Any]] = {}
        for req in requirements:
            key = req["name"]
            if key in seen:
                logger.warning(f"UI 名称重复：{key}，已合并元素")
                seen[key]["elements"].extend(req["elements"])
                seen[key]["interactions"].extend(req["interactions"])
                continue
            seen[key] = req
        return list(seen.values())

    @staticmethod
    def _normalize_requirement(
        raw: Any, ui_type: str, forced_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """把 GDM 里形态各异的 UI 条目规整成统一结构。"""
        # 非 dict 条目（裸字符串）由统一归一化规则整串当作 name
        raw = as_item(raw)

        name = forced_name or raw.get("name") or to_pascal_case(ui_type)
        name = to_pascal_case(str(name), fallback=to_pascal_case(ui_type))

        # elements 兼容 list[dict] / dict[name -> spec] / list[str]
        elements = UIGeneratorAgent._normalize_elements(raw.get("elements"))
        interactions = [i for i in (raw.get("interactions") or []) if isinstance(i, dict)]
        layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}

        return {
            "name": name,
            "type": str(raw.get("type") or ui_type).lower(),
            "elements": elements,
            "layout": dict(layout),
            "interactions": interactions,
            "description": raw.get("description", ""),
        }

    @staticmethod
    def _normalize_elements(raw: Any) -> List[Dict[str, Any]]:
        """把 elements 归一化为 ``[{type, name, properties, role}]``。"""
        out: List[Dict[str, Any]] = []
        if not raw:
            return out

        if isinstance(raw, dict):
            items = [{"name": k, **(v if isinstance(v, dict) else {"type": v})}
                     for k, v in raw.items()]
        elif isinstance(raw, list):
            items = raw
        else:
            return out

        for idx, item in enumerate(items):
            if isinstance(item, str):
                out.append({"type": item, "name": f"{to_pascal_case(item)}{idx + 1}",
                            "properties": {}})
                continue
            if not isinstance(item, dict):
                continue
            elem_type = item.get("type") or "Label"
            elem_name = item.get("name") or f"{to_pascal_case(str(elem_type))}{idx + 1}"
            props = item.get("properties")
            if not isinstance(props, dict):
                # 允许把 text/size 直接平铺在元素上
                props = {k: v for k, v in item.items() if k not in ("type", "name", "role")}
            out.append({
                "type": str(elem_type),
                "name": sanitize_node_name(str(elem_name), fallback=to_pascal_case(str(elem_type))),
                "properties": props,
                "role": item.get("role"),
            })
        return out

    # ─── 场景生成 ───────────────────────────────────────

    async def _generate_ui_scenes(
        self, requirements: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """批量生成 UI 场景。"""
        tasks = [self._generate_single_ui(req) for req in requirements]

        max_concurrent = max(1, int(self._ui_setting("max_concurrent_ui", DEFAULT_MAX_CONCURRENT_UI)))
        results: List[Any] = []
        for i in range(0, len(tasks), max_concurrent):
            batch = tasks[i: i + max_concurrent]
            results.extend(await asyncio.gather(*batch, return_exceptions=True))

        ui_scenes: List[Dict[str, Any]] = []
        for req, result in zip(requirements, results):
            if isinstance(result, dict) and "id" in result:
                ui_scenes.append(result)
            elif isinstance(result, BaseException):
                logger.error(f"UI 生成失败 {req.get('name')}: {result}")
        return ui_scenes

    async def _generate_single_ui(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        """生成单个 UI 场景（.tscn + 可选 .gd）。"""
        ui_name = requirement["name"]
        ui_type = requirement["type"]
        elements = requirement.get("elements", [])
        layout = requirement.get("layout", {})
        interactions = requirement.get("interactions", [])

        logger.info(f"生成 UI: {ui_name} ({ui_type})")

        root_type = resolve_root_type(ui_type)
        scene_file = f"{ui_name}.tscn"
        script_file = f"{sanitize_identifier(ui_name, prefix='ui')}.gd"
        script_res = f"{self.script_dir_res}/{script_file}"

        # 有交互才生成脚本，避免产出空壳 .gd
        script_content: Optional[str] = None
        attach_script: Optional[str] = None
        builder: UIBuilder = build_ui_scene(
            ui_name=ui_name,
            ui_type=ui_type,
            elements=elements,
            layout=layout,
            script_path=None,
        )
        if interactions:
            script_content = self._generate_ui_script(
                ui_name=ui_name,
                root_type=root_type,
                builder=builder,
                interactions=interactions,
            )
            if script_content:
                attach_script = script_res
                builder.set_script(attach_script)

        tscn_content = builder.build()

        scene_id = hashlib.md5(f"{ui_name}_{ui_type}".encode()).hexdigest()[:OBJECT_ID_LENGTH]

        return {
            "id": scene_id,
            "name": ui_name,
            "type": ui_type,
            "root_type": root_type,
            "tscn_content": tscn_content,
            "script_content": script_content,
            "file_path": f"{self.scene_dir_res}/{scene_file}",
            "script_path": attach_script,
            "node_count": len(builder.nodes),
            "element_count": len(elements),
            "interaction_count": len(interactions),
            "description": requirement.get("description", "") or f"{ui_type} UI",
        }

    # ─── 脚本生成 ───────────────────────────────────────

    def _generate_ui_script(
        self,
        ui_name: str,
        root_type: str,
        builder: UIBuilder,
        interactions: List[Dict[str, Any]],
    ) -> str:
        """生成 UI 交互 GDScript。

        与旧实现相比修正了三类会直接导致 Godot 报错的问题：

        - ``extends`` 跟随真实根类型（HUD 是 CanvasLayer，不是 Control）
        - 节点引用走 ``get_node_or_null`` + 类型注解，节点名不存在时不炸
        - interaction 的 ``action`` 不再原样拼进代码，改为白名单动作编译
        """
        class_name = to_pascal_case(ui_name, fallback="UI")
        # 根是 CanvasLayer 时脚本挂不到 Control 上，extends 必须匹配
        extends = root_type if root_type in ("Control", "CanvasLayer", "Window") else "Control"

        compiled = self._compile_interactions(builder, interactions)
        if not compiled:
            logger.warning(f"{ui_name}: interactions 中没有可编译的动作，跳过脚本生成")
            return ""

        lines: List[str] = [
            f"extends {extends}",
            "",
            f"class_name {class_name}",
            "",
            f"# {class_name} - 由 GameForge UIGenerator 自动生成",
            "# 请勿手动修改：重新生成会覆盖本文件",
            "",
        ]

        # signal 声明必须先于任何 .emit() 调用，否则 Godot 解析期就报
        # "Identifier xxx not declared"，整个脚本加载失败。
        signals: List[str] = []
        for entry in compiled:
            for sig in entry.get("declares_signals") or []:
                if sig not in signals:
                    signals.append(sig)
        if signals:
            lines.extend(f"signal {sig}" for sig in signals)
            lines.append("")

        # @onready 节点引用
        for entry in compiled:
            lines.append(entry["decl"])
        if compiled:
            lines.append("")

        # 信号连接
        lines.extend(["func _ready() -> void:"])
        for entry in compiled:
            lines.extend(entry["connect"])
        lines.append("")

        # 回调实现
        for entry in compiled:
            lines.extend(entry["handlers"])

        # 去尾随空行，保证文件以单个换行结束
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    def _compile_interactions(
        self, builder: UIBuilder, interactions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """把 GDM 的 interaction 条目编译成可写入 GDScript 的片段。

        返回每项包含 ``decl``（@onready 声明）、``connect``（_ready 里的连接行）、
        ``handlers``（回调函数体）。无法解析的条目会被跳过并告警。
        """
        compiled: List[Dict[str, Any]] = []
        used_callbacks: set = set()

        for interaction in interactions:
            raw_node = str(interaction.get("node") or interaction.get("target") or "").strip()
            if not raw_node:
                logger.warning("interaction 缺少 node 字段，已跳过")
                continue

            node_name = sanitize_node_name(raw_node, fallback="")
            node_path = builder.node_path(node_name)
            if not node_path:
                logger.warning(f"interaction 引用的节点 {raw_node!r} 不在场景里，已跳过")
                continue

            node_desc = builder.find_node(node_name) or {}
            node_type = node_desc.get("type", "Control")

            signal_name = str(interaction.get("signal") or "").strip().lower()
            if not signal_name:
                signal_name = _DEFAULT_SIGNAL_BY_TYPE.get(node_type, "pressed")
            if signal_name not in _ALLOWED_SIGNALS:
                logger.warning(f"信号 {signal_name!r} 不在白名单，回退为 pressed")
                signal_name = "pressed"

            callback = sanitize_identifier(
                str(interaction.get("callback") or f"on_{node_name}_{signal_name}"),
                prefix="on_ui",
            )
            # 同一节点多次绑定时避免函数重名
            base_callback = callback
            suffix = 2
            while callback in used_callbacks:
                callback = f"{base_callback}_{suffix}"
                suffix += 1
            used_callbacks.add(callback)

            var_name = sanitize_identifier(node_name, prefix="node")
            typed = _TYPED_DECL_BY_NODE.get(node_type, "Node")
            decl = f'@onready var {var_name}: {typed} = get_node_or_null("{node_path}")'

            # 回调签名要匹配信号的参数个数
            arg_sig = self._signal_signature(signal_name)
            connect = [
                f"{TAB}if {var_name} != null:",
                f'{TAB}{TAB}{var_name}.{signal_name}.connect({callback})',
            ]

            body = self._compile_action(interaction, var_name, node_path, node_type)
            handlers = [f"func {callback}({arg_sig}) -> void:", *body, ""]

            declared = self._declared_signal(interaction)
            compiled.append({
                "decl": decl,
                "connect": connect,
                "handlers": handlers,
                "node_path": node_path,
                "signal": signal_name,
                "declares_signals": [declared] if declared else [],
            })

        return compiled

    @staticmethod
    def _declared_signal(interaction: Dict[str, Any]) -> Optional[str]:
        """emit_signal 动作需要在 class 顶部声明的信号名，其余动作返回 None。"""
        action = str(interaction.get("action") or interaction.get("type") or "").strip().lower()
        if action != "emit_signal":
            return None
        base = sanitize_identifier(
            str(interaction.get("signal_name") or "ui_action"), prefix="ui"
        )
        return f"{base}_requested"

    @staticmethod
    def _signal_signature(signal_name: str) -> str:
        """返回信号回调的参数签名（Godot 4）。"""
        if signal_name in ("toggled",):
            return "button_pressed: bool"
        if signal_name in ("text_changed", "text_submitted"):
            return "new_text: String"
        if signal_name in ("value_changed",):
            return "value: float"
        if signal_name in ("item_selected",):
            return "index: int"
        if signal_name in ("gui_input",):
            return "event: InputEvent"
        return ""

    @staticmethod
    def _safe_comment(text: str, limit: int = 60) -> str:
        """把自由文本压成单行注释安全内容。

        GDM 里的 action 可能带换行/制表符，直接拼进 ``# TODO: ...`` 会让后半段
        逃逸成第 0 列的裸语句（Godot 解析必错），所以这里必须换行折叠 + 截断。
        """
        flat = re.sub(r"\s+", " ", str(text or "")).strip()
        flat = flat.lstrip("#").strip()
        return flat[:limit] or "未指定"

    def _compile_action(
        self,
        interaction: Dict[str, Any],
        var_name: str,
        node_path: str,
        node_type: str = "Control",
    ) -> List[str]:
        """把 interaction.action 编译成 GDScript 语句列表。

        只接受白名单动作；未知动作生成 TODO + pass，绝不把 GDM 的自由文本
        直接拼进代码（那样几乎必然产生语法错误，也会被 gd_guard 判失败）。
        依赖 ``button_pressed`` / ``grab_focus()`` 的动作会先校验节点类型，
        否则在 Label 之类的节点上会直接编译失败。
        """
        action = str(interaction.get("action") or interaction.get("type") or "").strip().lower()
        target = str(interaction.get("target_scene") or interaction.get("scene") or "").strip()
        is_button = node_type in _BUTTON_TYPES

        if action in ("change_scene", "load_scene", "goto", "open_scene"):
            if _SCENE_PATH_RE.match(target):
                return [
                    f'{TAB}get_tree().change_scene_to_file("{target}")',
                ]
            logger.warning(f"change_scene 目标非法或缺失：{target!r}")
            return [
                f"{TAB}# TODO: 补一个合法的 res://*.tscn 目标场景",
                f'{TAB}push_warning("{var_name}: change_scene 目标未配置")',
            ]

        if action in ("quit", "exit", "close_game"):
            return [f"{TAB}get_tree().quit()"]

        if action in ("pause", "toggle_pause"):
            return [f"{TAB}get_tree().paused = not get_tree().paused"]

        if action == "resume":
            return [f"{TAB}get_tree().paused = false"]

        if action in ("hide", "close", "hide_self"):
            return [f"{TAB}visible = false"]

        if action in ("show", "open", "show_self"):
            return [f"{TAB}visible = true"]

        if action in ("toggle_visibility",):
            return [f"{TAB}visible = not visible"]

        if action in ("toggle", "set_pressed"):
            if not is_button:
                logger.warning(f"{node_path} 不是按钮类节点，无法执行 {action}")
                return [
                    f"{TAB}# TODO: {node_type} 不支持 {self._safe_comment(action)}",
                    f"{TAB}pass",
                ]
            return [f"{TAB}{var_name}.button_pressed = not {var_name}.button_pressed"]

        if action in ("focus", "grab_focus"):
            if node_type not in CONTROL_TYPES:
                return [
                    f"{TAB}# TODO: {node_type} 不是 Control，无法 grab_focus",
                    f"{TAB}pass",
                ]
            return [f"{TAB}{var_name}.grab_focus()"]

        if action == "hide_node":
            node = sanitize_node_name(str(interaction.get("node_target") or ""), fallback="")
            if node:
                return [
                    f'{TAB}var target := get_node_or_null("{node}")',
                    f"{TAB}if target is CanvasItem:",
                    f'{TAB}{TAB}(target as CanvasItem).visible = false',
                ]

        if action == "emit_signal":
            sig = sanitize_identifier(
                str(interaction.get("signal_name") or "ui_action"), prefix="ui"
            )
            # 注意：signal 必须真的在 class 顶部声明，否则 Godot 直接 Parse Error
            # （"Identifier xxx not declared in the current scope"）导致脚本加载失败。
            # 声明由 _generate_ui_script 依据 entry["declares_signals"] 统一产出。
            return [f"{TAB}{sig}_requested.emit()"]

        # 兜底：保留可读的 TODO，让 Debugger/人工接手
        hint = self._safe_comment(action)
        return [
            f"{TAB}# TODO: 实现 {node_path} 的 {hint} 逻辑",
            f"{TAB}pass",
        ]

    # ─── 文件写入 ───────────────────────────────────────

    async def _write_ui_files(
        self, ui_scenes: List[Dict[str, Any]], state: GameDevState
    ) -> None:
        """写入 .tscn 与 .gd 到项目目录。"""
        project_context = state.get("project_context") or {}
        project_root = Path(project_context.get("project_root", "."))

        for scene in ui_scenes:
            tscn_path = self._resolve_path(project_root, scene["file_path"])
            tscn_path.parent.mkdir(parents=True, exist_ok=True)
            tscn_path.write_text(scene["tscn_content"], encoding="utf-8")
            logger.info(f"写入 UI 场景: {tscn_path}")

            if scene.get("script_content") and scene.get("script_path"):
                script_path = self._resolve_path(project_root, scene["script_path"])
                script_path.parent.mkdir(parents=True, exist_ok=True)
                script_path.write_text(scene["script_content"], encoding="utf-8")
                logger.info(f"写入 UI 脚本: {script_path}")

    @staticmethod
    def _resolve_path(project_root: Path, res_path: str) -> Path:
        """把 res:// 路径转换为项目内真实路径。"""
        return project_root / res_path.replace("res://", "")

    # ─── 只读辅助（供其他 Agent / 工具复用）─────────────

    @staticmethod
    def infer_menu_action(label: str) -> Dict[str, Any]:
        """按按钮文案推断一个合理的菜单动作。

        让 ``build_menu_scene`` 产出的脚本开箱可用，而不是一堆 TODO。
        """
        text = (label or "").strip().lower()
        if any(k in text for k in ("quit", "exit", "退出", "结束游戏")):
            return {"action": "quit"}
        if any(k in text for k in ("resume", "continue", "继续")):
            return {"action": "resume"}
        if any(k in text for k in ("pause", "暂停")):
            return {"action": "toggle_pause"}
        if any(k in text for k in ("back", "close", "返回", "关闭")):
            return {"action": "hide"}
        if any(k in text for k in ("start", "play", "开始", "进入")):
            return {"action": "change_scene", "target_scene": ""}
        return {"action": ""}

    @staticmethod
    def build_menu_scene(
        ui_name: str,
        buttons: List[str],
        title: Optional[str] = None,
    ) -> Dict[str, str]:
        """快捷生成一个居中竖直菜单，返回 {tscn, script}。

        便于 CLI / 测试 / MCP 工具在不走完整 workflow 的情况下拿到可用 UI。
        按钮文案会被 :meth:`infer_menu_action` 映射成 quit/resume/pause 等动作。
        """
        elements = [
            {"type": "Button", "name": sanitize_node_name(b, fallback="Button"),
             "properties": {"text": b}}
            for b in buttons
        ]
        interactions = []
        for label in buttons:
            node = sanitize_node_name(label, fallback="Button")
            interactions.append({"node": node, "signal": "pressed",
                                 **UIGeneratorAgent.infer_menu_action(label)})

        builder = build_ui_scene(
            ui_name=ui_name, ui_type="menu", elements=elements,
            layout={"title": title},
        )
        agent = UIGeneratorAgent(config={})
        script = agent._generate_ui_script(
            ui_name=ui_name, root_type="Control", builder=builder, interactions=interactions
        )
        if script:
            script_file = f"{sanitize_identifier(ui_name, prefix='ui')}.gd"
            builder.set_script(f"{UIGeneratorAgent.SCRIPT_DIR}/{script_file}")
        return {"tscn": builder.build(), "script": script}


__all__ = ["UIGeneratorAgent", "GodotUITools", "UIBuilder"]
