"""GameForge - 轻量 Godot 知识库检索（多智能体改造第四步用）

读取 data/ 下的 Godot 知识 JSON（godot_knowledge / godot_knowledge_extra /
godot_knowledge_generation），按关键词重叠做轻量 RAG 检索，供 debugger 在
遇到陌生报错时「委派调研子 agent」查知识库回收结论。

不依赖外部向量库，纯离线、可控、可测试。
"""

import json
import os
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_KNOWLEDGE_FILES = [
    "data/godot_knowledge.json",
    "data/godot_knowledge_extra.json",
    "data/godot_knowledge_generation.json",
]

_cache: Optional[List[Dict[str, Any]]] = None


def _load_entries() -> List[Dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    entries: List[Dict[str, Any]] = []
    for rel in _KNOWLEDGE_FILES:
        path = os.path.join(_PROJECT_ROOT, rel)
        if not os.path.exists(path):
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
