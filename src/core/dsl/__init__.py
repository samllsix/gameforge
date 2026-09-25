"""GameForge - Game DSL 模块

将结构化游戏规格序列化为 YAML DSL，
作为 Agent 间通信和人类可编辑的规范格式。
"""

import json
from typing import Any, Dict, Optional

from src.core.state.game_state import GameDevState


class GameDSL:
    """游戏定义语言（DSL）管理类

    负责：
    - 将 Game Spec / GDM 序列化为 YAML
    - 从 YAML 反序列化为结构化字典
    - 提供人类可编辑的游戏规范格式
    """

    @staticmethod
    def from_spec(spec: Dict[str, Any]) -> str:
        """将 Game Spec 转换为 YAML DSL 字符串"""
        return GameDSL._to_yaml(spec)

    @staticmethod
    def to_spec(yaml_text: str) -> Dict[str, Any]:
        """从 YAML DSL 字符串解析为 Game Spec"""
        return GameDSL._from_yaml(yaml_text)

    @staticmethod
    def validate(spec: Dict[str, Any]) -> bool:
        """校验 Game Spec 是否包含必要字段"""
        if not isinstance(spec, dict):
            return False
        return "game" in spec or "genre" in spec

    @staticmethod
    def merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """合并两个 Game Spec，override 覆盖 base"""
        result = dict(base)
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = GameDSL.merge(result[key], value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _to_yaml(data: Dict[str, Any]) -> str:
        """将字典转换为 YAML 格式（简化实现，避免额外依赖）"""
        lines = ["game:"]
        game = data.get("game", {})
        lines.append(f"  genre: {game.get('genre', 'casual')}")
        lines.append(f"  camera: {game.get('camera', '2D_side')}")
        lines.append(f"  difficulty: {game.get('difficulty', 'medium')}")

        player = data.get("player", {})
        if player:
            lines.append("player:")
            lines.append(f"  hp: {player.get('hp', 100)}")
            actions = player.get("actions", [])
            if actions:
                lines.append(f"  actions: {json.dumps(actions, ensure_ascii=False)}")
            resources = player.get("resources", [])
            if resources:
                lines.append(f"  resources: {json.dumps(resources, ensure_ascii=False)}")

        enemy = data.get("enemy", {})
        if enemy:
            lines.append("enemy:")
            types = enemy.get("types", [])
            if types:
                lines.append(f"  types: {json.dumps(types, ensure_ascii=False)}")
            lines.append(f"  wave_system: {str(enemy.get('wave_system', False)).lower()}")

        mechanics = data.get("mechanics", [])
        if mechanics:
            lines.append("mechanics:")
            for mechanic in mechanics:
                lines.append(f"  - {mechanic}")

        assets = data.get("assets", [])
        if assets:
            lines.append("assets:")
            for asset in assets:
                lines.append(f"  - {asset}")

        scenes = data.get("scenes", [])
        if scenes:
            lines.append("scenes:")
            for scene in scenes:
                lines.append(f"  - {scene}")

        return "\n".join(lines)

    @staticmethod
    def _from_yaml(text: str) -> Dict[str, Any]:
        """从 YAML 格式解析为字典（简化实现）"""
        result: Dict[str, Any] = {}
        current_section = None
        current_list_key = None

        for line in text.split("\n"):
            if not line.strip() or line.strip().startswith("#"):
                continue

            stripped = line.rstrip()
            indent = len(line) - len(line.lstrip())
            content = stripped.lstrip()

            if indent == 0:
                if content.endswith(":"):
                    current_section = content[:-1].strip()
                    result[current_section] = {}
                    current_list_key = None
                continue

            if indent >= 2 and content.startswith("- "):
                item = content[2:].strip()
                if current_section:
                    result.setdefault(current_section, {}).setdefault(current_list_key or "_items", []).append(GameDSL._coerce(item))
                continue

            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                if not value and stripped.endswith(":"):
                    current_list_key = key
                    continue

                parsed = GameDSL._coerce(value)

                if current_section:
                    result.setdefault(current_section, {})[key] = parsed
                else:
                    result[key] = parsed

        # 扁平化 _items
        flattened = {}
        for section, content in result.items():
            if isinstance(content, dict) and "_items" in content and len(content) == 1:
                flattened[section] = content["_items"]
            else:
                flattened[section] = content

        return flattened

    @staticmethod
    def _coerce(value: str) -> Any:
        """尝试将字符串转换为合适的 Python 类型"""
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        if (value.startswith("[") and value.endswith("]")) or (value.startswith('"[') and value.endswith(']"')):
            try:
                return json.loads(value)
            except ValueError:
                pass
        if (value.startswith("{") and value.endswith("}")) or (value.startswith('"{') and value.endswith('}"')):
            try:
                return json.loads(value)
            except ValueError:
                pass
        return value.strip('"')
