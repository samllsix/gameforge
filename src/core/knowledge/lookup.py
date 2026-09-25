"""GameForge - 轻量 Godot 知识库检索

按目录优先级读取 Godot 知识 JSON（列表 / {"entries": [...]} / {key: {...}} 三种
结构都兼容），按关键词重叠做轻量 RAG 检索，供 CodeGenerator 在遇到陌生报错时
查知识库回收结论。

1. ``config/knowledge/*.json`` —— 随仓库发行的内置知识库（默认包含，勿放用户数据）
2. ``data/godot_knowledge*.json`` —— 用户自定义知识（data/ 默认被 gitignore，
   可安全放置私有/团队知识，覆盖内置同名 index）

不依赖外部向量库，纯离线、可控、可测试。
"""

import json
import os
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

#: 随仓库发行的内置知识（git 跟踪）
BUILTIN_KNOWLEDGE_DIR = os.path.join(_PROJECT_ROOT, "config", "knowledge")

#: 用户自定义知识（gitignore，可放置私有知识）；保留历史 data/ 路径兼容
USER_KNOWLEDGE_FILES = [
    "data/godot_knowledge.json",
    "data/godot_knowledge_extra.json",
    "data/godot_knowledge_generation.json",
]

_cache: Optional[List[Dict[str, Any]]] = None
_cache_key: Optional[str] = None


def _knowledge_key() -> str:
    """识别知识源是否变化（测试/热更新时按 mtime 失效缓存）。"""
    key = BUILTIN_KNOWLEDGE_DIR + "|" + "|".join(
        os.path.join(_PROJECT_ROOT, rel) for rel in USER_KNOWLEDGE_FILES
    )
    try:
        return key + "@" + ",".join(
            sorted(
                f"{p}:{os.path.getmtime(p):.0f}"
                for p in [os.path.join(_PROJECT_ROOT, rel) for rel in USER_KNOWLEDGE_FILES]
                + _builtin_json_paths()
                if os.path.isfile(p)
            )
        )
    except OSError:
        return key


def _builtin_json_paths() -> List[str]:
    """config/knowledge/ 下的所有 JSON（不存在/不可读时返回空列表）。"""
    try:
        if not os.path.isdir(BUILTIN_KNOWLEDGE_DIR):
            return []
        return [
            os.path.join(BUILTIN_KNOWLEDGE_DIR, name)
            for name in sorted(os.listdir(BUILTIN_KNOWLEDGE_DIR))
            if name.endswith(".json")
        ]
    except OSError:
        return []


def _load_entries() -> List[Dict[str, Any]]:
    global _cache, _cache_key
    key = _knowledge_key()
    if _cache is not None and _cache_key == key:
        return _cache
    entries: List[Dict[str, Any]] = []
    for path in _builtin_json_paths() + [
        os.path.join(_PROJECT_ROOT, rel) for rel in USER_KNOWLEDGE_FILES
    ]:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, list):
            entries.extend(data)
        elif isinstance(data, dict):
            # 兼容 { "entries": [...] } 或 { "key": {...} } 结构
            if "entries" in data and isinstance(data["entries"], list):
                entries.extend(data["entries"])
            else:
                for k, v in data.items():
                    if isinstance(v, dict):
                        v.setdefault("title", k)
                        entries.append(v)
    _cache = entries
    _cache_key = key
    return entries


def _tokenize(text: str) -> List[str]:
    import re

    return [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower()) if len(t) > 2]


def lookup_godot_knowledge(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """按关键词重叠对知识库做轻量检索，返回 top_k 条。"""
    entries = _load_entries()
    if not entries:
        return []
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return entries[:top_k]

    scored: List[Dict[str, Any]] = []
    for entry in entries:
        text = " ".join(
            str(entry.get(k, "")) for k in ("title", "content", "description", "tags", "summary")
        )
        e_tokens = set(_tokenize(text))
        overlap = len(q_tokens & e_tokens)
        if overlap == 0:
            continue
        # 标题命中权重更高
        title_hit = len(q_tokens & set(_tokenize(str(entry.get("title", ""))))) * 2
        scored.append((overlap + title_hit, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:top_k]]
