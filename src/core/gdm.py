"""GDM（Game Design Model）条目形态归一化。

GDM 由 LLM 产出或由人工编写，同一个列表字段（``main_systems`` /
``entities`` / ``code_modules`` / ``dialogues`` / ``sfx`` / ``menus`` /
``ui_panels`` ...）里的条目往往是「dict 与裸字符串混用」：

- ``dict``：直接使用；
- 裸 ``str``：整串当作条目名称（音频/UI 场景下同时也当作描述）。

game_designer / audio_generator / ui_generator 过去各自复制了一份
「dict-or-str 容错」逻辑，行为细节略有出入。本模块把所有下游 Agent 收拢
到同一套规则，避免三处行为漂移：

1. dict 条目原样返回（不深拷贝，允许调用方原地补字段）；
2. 非 dict、非 str 的条目在列表归一化时直接丢弃（旧实现同样跳过这类条目）；
3. ``defaults`` 只在条目缺少对应键时补齐，不覆盖已有值。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


def as_item(
    raw: Any,
    name_key: str = "name",
    *,
    description_key: Optional[str] = None,
    defaults: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """把单个 GDM 条目规整为 dict。

    Args:
        raw: GDM 条目，dict 原样返回；其余标量转成名称字典。
        name_key: 标量条目的名称字段名（如 code_modules 用 ``module_name``）。
        description_key: 给出时，标量条目会同时写入 ``{description_key: str(raw)}``。
        defaults: 标量条目缺失字段的默认值，按键补齐，不覆盖已有键。

    Returns:
        规整后的 dict 条目。

    Examples:
        >>> as_item("jump")
        {'name': 'jump'}
        >>> as_item("jump", description_key="description")
        {'name': 'jump', 'description': 'jump'}
        >>> as_item({"name": "x"}, defaults={"priority": "medium"})
        {'name': 'x', 'priority': 'medium'}
    """
    if isinstance(raw, dict):
        return raw

    item: Dict[str, Any] = {name_key: str(raw)}
    if description_key:
        item[description_key] = str(raw)
    if defaults:
        for key, value in defaults.items():
            item.setdefault(key, value)
    return item


def as_item_list(
    items: Optional[Iterable[Any]],
    name_key: str = "name",
    *,
    description_key: Optional[str] = None,
    defaults: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """把 GDM 列表字段逐条规整为 dict 列表。

    与 :func:`as_item` 规则一致；既不是 dict 也不是 str 的条目（如数字）
    直接丢弃，保持与旧实现「只接受 str/dict」的行为。

    Args:
        items: GDM 列表字段（可为 None）。
        name_key / description_key / defaults: 透传给 :func:`as_item`。

    Returns:
        规整后的条目列表，顺序保持输入顺序。
    """
    out: List[Dict[str, Any]] = []
    for raw in items or []:
        if isinstance(raw, (dict, str)):
            out.append(
                as_item(
                    raw,
                    name_key,
                    description_key=description_key,
                    defaults=defaults,
                )
            )
    return out


__all__ = ["as_item", "as_item_list"]
