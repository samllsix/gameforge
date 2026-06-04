"""GameForge - 统一JSON提取模块

从LLM响应中提取JSON的公共实现，支持多种回退策略。
"""

import re
import json
from typing import Any, Dict, Optional

# 预编译的正则表达式（模块级常量）
_JSON_BLOCK_PATTERNS = [
    re.compile(r"```json\s*\n([\s\S]*?)\n```", re.IGNORECASE),
    re.compile(r"```\s*\n([\s\S]*?)\n```"),
]

_GAME_OBJECTS_PATTERN = re.compile(r"(\{[\s\S]*\"game_objects\"[\s\S]*\})")
_GAME_TITLE_PATTERN = re.compile(r"(\{[\s\S]*\"game_title\"[\s\S]*\})")


def extract_json(text: str, fallback_to_raw: bool = False) -> Optional[Dict[str, Any]]:
    """从文本中提取JSON

    提取策略（按优先级）：
    1. 直接解析整个文本
    2. 从 markdown code block 中提取
    3. 匹配包含特定字段的JSON对象
    4. 提取第一个 { 到最后一个 } 之间的内容

    Args:
        text: 待提取的文本
        fallback_to_raw: 解析失败时是否返回 {"raw_response": text, "parse_error": True}
                         为 False 时返回 None

    Returns:
        解析后的JSON字典，或 None / raw_response
    """
    if not text or not text.strip():
        return {"raw_response": text, "parse_error": True} if fallback_to_raw else None

    # 策略1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略2: 从 code block 中提取
    for pattern in _JSON_BLOCK_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    # 策略3: 匹配包含特定字段的JSON对象
    for pattern in [_GAME_OBJECTS_PATTERN, _GAME_TITLE_PATTERN]:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    # 策略4: 提取第一个 { 到最后一个 }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    # 所有策略失败
    return {"raw_response": text, "parse_error": True} if fallback_to_raw else None


def extract_json_strict(text: str) -> Dict[str, Any]:
    """严格模式提取JSON - 失败时总是返回含parse_error的字典

    Args:
        text: 待提取的文本

    Returns:
        解析后的JSON字典，或 {"raw_response": text, "parse_error": True}
    """
    return extract_json(text, fallback_to_raw=True)
