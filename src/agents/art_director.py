"""GameForge - 美术指导器（主题驱动的素材规划）

与 genre_fusion 同一个发散思路：不穷举素材，而是
「9 个素材槽位 × 25 个主题包（母题词）」= 225 种主题化素材组合，
由一次廉价的 LLM 调用产出整份《美术指导书》（各槽位的生图提示词），
LLM 不可用时回落到「母题组合模板」（仍然主题化，只是少了点灵气）。

产出直接喂给 asset_forge（风格约束仍由生图漏斗统一追加）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()

# 素材槽位 → 母题回退模板（{m1}/{m2} 取主题包母题词，{mood} 取主题名）
_FALLBACK_TEMPLATES: Dict[str, str] = {
    "background": "wide parallax pixel art {m1} landscape with {m2}, no characters, no text",
    "player": "single {m1} themed hero character game sprite, full body, front view, centered in frame",
    "enemy": "single {m1} themed enemy creature game sprite, mischievous look, centered in frame",
    "pickup": "single collectible {m2} themed item game sprite, slightly glowing, centered",
    "ground": "seamless tileable pixel art {m1} ground texture, top-down, uniform lighting, no borders",
    "platform": "seamless tileable pixel art {m2} platform texture, uniform lighting, no borders",
    "decoration": "single {m2} themed scenery prop game sprite, centered in frame",
    "npc": "single {m1} themed villager NPC game sprite, full body, centered in frame",
    "icon": "square game app icon, {m1} themed emblem, bold silhouette, centered, no text",
}

_REQUIRED_SLOTS = set(_FALLBACK_TEMPLATES.keys())

# 主题包 → 槽位名词（英语，供母题回退模板拼装；LLM 规划可用时会整体覆盖）
_THEME_NOUNS: Dict[str, Dict[str, str]] = {
    "farm": {"char": "farmer", "foe": "crow", "item": "crop", "ground": "grass", "platform": "wooden"},
    "space_night": {"char": "astronaut", "foe": "alien drone", "item": "energy cell", "ground": "metal deck", "platform": "steel"},
    "neon_city": {"char": "street hacker", "foe": "security drone", "item": "data chip", "ground": "wet asphalt", "platform": "neon sign"},
    "dungeon": {"char": "knight", "foe": "skeleton", "item": "gem", "ground": "stone brick", "platform": "stone slab"},
    "ocean": {"char": "deep diver", "foe": "shark", "item": "pearl", "ground": "sandy seabed", "platform": "coral"},
    "jungle": {"char": "explorer", "foe": "jaguar", "item": "fruit", "ground": "mossy earth", "platform": "vine log"},
    "snow": {"char": "snow explorer", "foe": "ice wolf", "item": "ice crystal", "ground": "snow", "platform": "ice slab"},
    "desert": {"char": "desert nomad", "foe": "giant scorpion", "item": "ancient relic", "ground": "sandstone", "platform": "sun-baked brick"},
    "volcano": {"char": "lava knight", "foe": "magma beast", "item": "ember", "ground": "basalt", "platform": "obsidian"},
    "steampunk": {"char": "inventor", "foe": "clockwork spider", "item": "brass gear", "ground": "riveted iron", "platform": "gear plate"},
    "medieval": {"char": "squire", "foe": "bandit", "item": "banner crest", "ground": "cobblestone", "platform": "timber"},
    "fairy_tale": {"char": "little fairy", "foe": "goblin", "item": "cupcake", "ground": "frosting meadow", "platform": "biscuit"},
    "stone_age": {"char": "tribal hunter", "foe": "raptor", "item": "dino egg", "ground": "packed dirt", "platform": "rough stone"},
    "wasteland": {"char": "scavenger", "foe": "mutant hound", "item": "scrap part", "ground": "cracked concrete", "platform": "rusted plate"},
    "sky_islands": {"char": "sky captain", "foe": "storm harpy", "item": "cloud shard", "ground": "cloudstone", "platform": "floating rock"},
    "bug_world": {"char": "ant rider", "foe": "beetle", "item": "honey drop", "ground": "leaf floor", "platform": "twig"},
    "wild_west": {"char": "cowboy", "foe": "outlaw", "item": "sheriff star", "ground": "dusty wood", "platform": "saloon plank"},
    "wuxia": {"char": "wandering swordsman", "foe": "bandit chief", "item": "jade token", "ground": "bamboo grove floor", "platform": "stone pillar"},
    "graveyard": {"char": "gravedigger", "foe": "wraith", "item": "soul lantern", "ground": "dead grass", "platform": "tombstone"},
    "campus": {"char": "student", "foe": "hall monitor", "item": "notebook", "ground": "checkered tile", "platform": "desk"},
    "ramen": {"char": "ramen chef", "foe": "hungry spirit", "item": "golden egg topping", "ground": "tatami", "platform": "counter"},
    "egypt": {"char": "pharaoh guard", "foe": "mummy", "item": "scarab", "ground": "sandstone brick", "platform": "gilded slab"},
    "racing_circuit": {"char": "racer", "foe": "rival racer", "item": "turbo boost", "ground": "asphalt track", "platform": "tire stack"},
    "bakery": {"char": "baker", "foe": "sourdough gremlin", "item": "golden bun", "ground": "bakery tile", "platform": "bread loaf"},
    "classic_arena": {"char": "pixel fighter", "foe": "pixel slime", "item": "pixel coin", "ground": "grid floor", "platform": "grid block"},
}


def _find_theme_pack(theme: Optional[str]) -> Optional[Dict[str, str]]:
    """按 SceneIR.theme 匹配主题包（palette_base 或 id）。"""
    if not theme:
        return None
    from src.agents.genre_fusion import THEME_PACKS

    for pack in THEME_PACKS:
        if pack["id"] == theme or pack["palette_base"] == theme:
            return pack
    return None


def _fallback_plan(scene_ir: Any) -> Dict[str, str]:
    """母题组合模板：主题包名词/母题词填槽（零 LLM，依旧主题化）。

    槽位名词表（char/foe/item/ground...）优先，剩余槽位用母题词补位。
    """
    pack = _find_theme_pack(getattr(scene_ir, "theme", None))
    nouns = _THEME_NOUNS.get(pack["id"], {}) if pack else {}
    motifs = (pack["motifs"].split() if pack else [])
    m1 = nouns.get("char") or (motifs[0] if motifs else "cozy village")
    m2 = nouns.get("foe") or (motifs[1] if len(motifs) > 1 else m1)
    mood = pack["name_zh"] if pack else "cozy"
    plan = {
        slot: tpl.format(m1=m1, m2=m2, mood=mood)
        for slot, tpl in _FALLBACK_TEMPLATES.items()
    }
    # 背景用场景词（母题第二个词通常是地标：谷仓/风车/神庙），不用角色词
    scene_word = (motifs[1] if len(motifs) > 1 else None) or nouns.get("ground") or m1
    plan["background"] = _FALLBACK_TEMPLATES["background"].format(m1=scene_word, m2=m2, mood=mood)
    # 槽位专名覆盖（比母题词更贴角色）
    overrides = {
        "ground": nouns.get("ground"), "platform": nouns.get("platform"),
        "enemy": nouns.get("foe"), "pickup": nouns.get("item"),
    }
    for slot, noun in overrides.items():
        if noun:
            plan[slot] = _FALLBACK_TEMPLATES[slot].format(m1=noun, m2=noun, mood=mood)
    if pack:
        logger.info("art_director.fallback_plan", theme=pack["id"], slots=len(plan))
    return plan


def plan_art(scene_ir: Any) -> Dict[str, str]:
    """规划整份美术指导书：{素材槽位: 英文生图提示}。

    LLM 一次调用产出全部槽位（比逐槽 smart_prompt 便宜 9 倍），
    输出缺槽/解析失败 → 按槽回落母题组合模板，保证恒有可用方案。
    风格约束（星露谷像素风）不在这里写——由生图漏斗统一追加。
    """
    fallback = _fallback_plan(scene_ir)
    if os.getenv("GAMEFORGE_SMART_PROMPTS", "1").strip().lower() in {"0", "false", "no"}:
        return fallback

    pack = _find_theme_pack(getattr(scene_ir, "theme", None))
    motifs = pack["motifs"] if pack else "cozy village"
    genre = getattr(scene_ir, "genre", "platformer")
    try:
        from src.utils.llm_client import get_llm_client

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        import yaml

        cfg_path = os.path.join(repo_root, "config", "config.yaml")
        llm_config = {}
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                llm_config = yaml.safe_load(f) or {}
        client = get_llm_client(llm_config)
        reply = client.chat_sync(
            messages=[{"role": "user", "content": (
                f'You are the art director of a {genre} pixel-art game themed "{motifs}". '
                f"Write one short English image prompt (max 18 words, no style keywords, "
                f'no quotes) for each asset slot. Reply with STRICT JSON only: '
                f'{{"background":"...","player":"...","enemy":"...","pickup":"...",'
                f'"ground":"...","platform":"...","decoration":"...","npc":"...","icon":"..."}}'
            )}],
            max_tokens=300,
            temperature=0.8,
        )
        import json

        text = (reply or "").strip()
        # 剥掉可能的 ```json 围栏
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        plan = json.loads(text)
        if isinstance(plan, dict):
            clean = {k: str(v).strip() for k, v in plan.items()
                     if k in _REQUIRED_SLOTS and v and len(str(v)) < 300}
            # 缺槽回落
            for slot in _REQUIRED_SLOTS:
                clean.setdefault(slot, fallback[slot])
            logger.info("art_director.llm_plan", theme=motifs, slots=len(clean))
            return clean
    except Exception as e:  # noqa: BLE001
        logger.warning("art_director.llm_plan_failed", error=str(e))
    return fallback
