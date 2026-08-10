"""GameForge - .tscn 文件格式序列化器

Godot 文本场景格式 (.tscn) 的序列化工具。
支持生成符合 Godot 4.x 规范的 .tscn 文件。
"""

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class TSCNResource:
    """tscn 资源定义"""
    type: str
    id: str
    path: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_string(self) -> str:
        if self.path:
            attrs = f'type="{self.type}" path="{self.path}" id="{self.id}"'
        else:
            attrs = f'type="{self.type}" id="{self.id}"'
        return f'[ext_resource {attrs}]'


@dataclass
class TSCNSubResource:
    """tscn 子资源定义"""
    type: str
    id: str
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_string(self) -> str:
        lines = [f'[sub_resource type="{self.type}" id="{self.id}"]']
        for key, value in self.properties.items():
            lines.append(f'{key} = {self._format_value(value)}')
        return "\n".join(lines)

    def _format_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, (list, tuple)):
            if len(value) == 2:
                return f'Vector2({value[0]}, {value[1]})'
            elif len(value) == 3:
                return f'Vector3({value[0]}, {value[1]}, {value[2]})'
            elif len(value) == 4:
                return f'Color({value[0]}, {value[1]}, {value[2]}, {value[3]})'
            return str(value)
        return str(value)


@dataclass
class TSCNNode:
    """tscn 节点定义"""
    name: str
    type: str
    parent: str = "."
    properties: Dict[str, Any] = field(default_factory=dict)
    children: List["TSCNNode"] = field(default_factory=list)

    def to_lines(self, resource_path: str = "") -> List[str]:
        lines = []
        parent_attr = f' parent="{self.parent}"' if self.parent != "." else ""
        lines.append(f'[node name="{self.name}" type="{self.type}"{parent_attr}]')

        for key, value in self.properties.items():
            lines.append(f'{key} = {self._format_value(value)}')

        for child in self.children:
            child.parent = f'{self.parent}/{self.name}' if self.parent != "." else self.name
            lines.extend(child.to_lines(resource_path))

        return lines

    def _format_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, str):
            if value.startswith("ExtResource") or value.startswith("SubResource"):
                return value
            return f'"{value}"'
        elif isinstance(value, (list, tuple)):
            if len(value) == 2:
                return f'Vector2({value[0]}, {value[1]})'
            elif len(value) == 3:
                return f'Vector3({value[0]}, {value[1]}, {value[2]})'
            elif len(value) == 4:
                return f'Color({value[0]}, {value[1]}, {value[2]}, {value[3]})'
            return str(value)
        return str(value)


class TSCNWriter:
    """tscn 文件写入器

    将 TSCNNode 树序列化为 .tscn 文件内容。
    """

    def __init__(self):
        self._resource_counter = 0
        self._sub_resource_counter = 0

    def write(self, root_node: TSCNNode,
              resources: Optional[List[TSCNResource]] = None,
              sub_resources: Optional[List[TSCNSubResource]] = None,
              resource_path: str = "") -> str:
        """生成 .tscn 文件内容

        Args:
            root_node: 根节点
            resources: 外部资源列表
            sub_resources: 子资源列表
            resource_path: 资源文件路径

        Returns:
            .tscn 文件内容
        """
        load_steps = 1 + len(resources or []) + len(sub_resources or [])
        format_version = 3  # Godot 4.x

        lines = [
            f'[gd_scene load_steps={load_steps} format={format_version}]',
            '',
        ]

        # 外部资源
        for res in (resources or []):
            lines.append(res.to_string())
        if resources:
            lines.append('')

        # 子资源
        for sub in (sub_resources or []):
            lines.append(sub.to_string())
        if sub_resources:
            lines.append('')

        # 节点树
        lines.extend(root_node.to_lines(resource_path))

        content = "\n".join(lines)
        self._validate_tscn_order(content)
        return content

    @staticmethod
    def _validate_tscn_order(content: str) -> None:
        """存盘前自检：确保所有 [sub_resource] 出现在首个 [node] 之前。

        Godot TSCN 强制顺序为 [gd_scene] -> [ext_resource] -> [sub_resource] -> [node]，
        若 sub_resource 落在 node 之后，Godot 加载场景时会直接失败。
        """
        lines = content.split("\n")
        first_node_idx = None
        for i, line in enumerate(lines):
            if line.startswith("[node"):
                first_node_idx = i
                break
        if first_node_idx is None:
            return
        for i, line in enumerate(lines):
            if line.startswith("[sub_resource") and i > first_node_idx:
                raise RuntimeError(
                    "TSCN 顺序错误：sub_resource 出现在首个 [node] 之后（行 %d）" % (i + 1)
                )

    def next_resource_id(self) -> str:
        """生成下一个资源 ID"""
        self._resource_counter += 1
        return str(self._resource_counter)

    def next_sub_resource_id(self) -> str:
        """生成下一个子资源 ID"""
        self._sub_resource_counter += 1
        return f'SubResource_{self._sub_resource_counter}'
