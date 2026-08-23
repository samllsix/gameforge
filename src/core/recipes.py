"""GameForge - 已验证游戏配方库（P1 语义级复用）

把"通过了运行时冒烟验证"的完整成功方案（GDM + 任务计划 + 代码 + 场景）沉淀为配方。
下次遇到同类/相同需求，在进入 LLM 主流水线之前命中，直接复用已验证成品，逼近 2s 目标。

关键安全约束：
- 只有 runnable=True（真机冒烟通过）的方案才会被写入，避免污染配方库。
- 命中即整体复用，不再跑 game_designer/planner/LLM codegen，只做场景落盘 + godot 构建 + 冒烟。

匹配策略（两级）：
1. exactly：需求归一化文本与配方完全一致的直接命中（同一需求复现，最可靠）。
2. fuzzy：提取需求中的玩法特征词，与配方的 key_terms 做集合重合度打分，
   重合 >=2 且覆盖率 >=0.6 命中（同类需求复用）。
"""

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 玩法特征词表（用于构造需求指纹）。命中两种语言均可，助同类需求归并。
_GAME_FEATURE_TERMS = [
    # 通用
    "2d", "3d", "godot", "玩法", "单机", "小游戏",
    # 玩法流派
    "平台", "跳跃", "跑酷", "射击", "解谜", "塔防", "防守",
    "rpg", "回合", "格斗", "赛车", "贪吃蛇", "打飞机", "自走棋",
    "动作", "休闲", "竞速", "养成",
    # 实体
    "玩家", "角色", "player", "敌人", "怪物", "enemy", "boss",
    "金币", "道具", "收集", "coin", "拾取",
    "子弹", "弹幕", "bullet", "障碍", "陷阱", "尖刺",
    "关卡", "level", "boss", "升级", "技能",
    "菜单", "hud", "ui", "ui", "音效", "音乐", "音频",
]

STOP_RE = re.compile(r"[\s,，。.;；:：!！?？'\"、/\\()\[\]【】]+")


def _normalize_text(text: str) -> str:
    """需求文本归一化：小写、去空白与标点、压缩。"""
    s = text.lower()
    s = STOP_RE.sub("", s)
    return s


def _key_terms(text: str) -> frozenset:
    """从需求文本中提取玩法特征词集合（需求指纹）。"""
    s = text.lower()
    return frozenset(w for w in _GAME_FEATURE_TERMS if w in s)


def _stable_token(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


class RecipeStore:
    """已验证配方存储与检索。"""

    def __init__(self, storage_dir: str = "data/recipes"):
        self.dir = Path(storage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 写入 ----------------

    def save_recipe(self, state: Dict[str, Any]) -> bool:
        """把已完成的成功 state 存为配方。返回是否写入。

        仅当调用方已确认真机冒烟通过（runnable is True）时调用——
        本方法本身也会做防御性校验，防止污染配方库。
        """
        if state.get("runnable") is not True:
            return False
        code_files = state.get("code_generated", {}) or {}
        if not code_files:
            return False

        requirements = (
            state.get("project_context", {}).get("requirements", "") or ""
        )
        gdm = state.get("game_design_model") or {}

        recipe = {
            "requirements": requirements,
            "requirements_norm": _normalize_text(requirements),
            "key_terms": sorted(_key_terms(requirements)),
            "title": gdm.get("game_title") or gdm.get("title") or "untitled",
            "genre": gdm.get("genre", ""),
            "game_design_model": gdm,
            "task_plan": state.get("task_plan", []),
            "code_files": code_files,
            "scene_description": state.get("scene_description"),
            "engine": "godot",
            "verified": True,
            "created_at": datetime.now().isoformat(),
        }

        key = _stable_token(_normalize_text(requirements), self._content_digest(code_files))
        path = self.dir / f"{key}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)
        return True

    @staticmethod
    def _content_digest(code_files: Dict[str, str]) -> str:
        blob = "".join(f"{k}\x00{v}" for k, v in sorted(code_files.items()))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ---------------- 检索 ----------------

    def search(self, requirements: str) -> Optional[Dict[str, Any]]:
        """按需求匹配已验证配方；命中返回配方，否则 None。

        先精确（归一化文本相同），再模糊（特征词重合度打分）。
        """
        if not requirements:
            return None
        norm = _normalize_text(requirements)
        recipes = self._load_all()

        # 1) 精确命中
        for r in recipes:
            if r.get("requirements_norm") == norm:
                return r

        # 2) 模糊命中
        kt = _key_terms(requirements)
        if not kt:
            return None
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for r in recipes:
            rkt = set(r.get("key_terms", []))
            if not rkt:
                continue
            # 匹配度 = 当前需求特征词中被配方覆盖的比例（召回视角）
            inter = len(kt & rkt)
            score = inter / len(kt)
            if inter >= 2 and score >= 0.6 and score > best_score:
                best = r
                best_score = score
        return best

    def _load_all(self) -> List[Dict[str, Any]]:
        recipes = []
        if not self.dir.is_dir():
            return recipes
        for path in self.dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    recipes.append(json.load(f))
            except Exception:
                continue
        return recipes

    # ---------------- 注入 ----------------

    @staticmethod
    def apply_recipe(state: Dict[str, Any], recipe: Dict[str, Any]) -> None:
        """把配方内容铺回 state，标记 recipe_hit，后续主图将被跳过。"""
        state["game_design_model"] = recipe.get("game_design_model")
        state["task_plan"] = recipe.get("task_plan", [])
        cf = state.setdefault("code_generated", {})
        for fpath, content in (recipe.get("code_files") or {}).items():
            cf[fpath] = content
        state["scene_description"] = recipe.get("scene_description")
        if recipe.get("scene_description"):
            state["scene_status"] = "success"
        # 配方已验证可运行，视为可运行状态
        state["runnable"] = True
        state["recipe_hit"] = True
        state["recipe_title"] = recipe.get("title", "")
        state["current_phase"] = "recipe_reused"
        state["is_complete"] = True