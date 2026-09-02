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


# 需求关键词 → 主题包（顺序即优先级，具体主题在前）。
# P0-1：SceneIR.theme 可能是游戏标题（模板路径）或与包不符的调色板名，
# 需求关键词是主题包匹配的最终兜底（太空→space_night，深海→ocean）。
_KEYWORD_THEMES = (
    (("太空", "宇宙", "星际", "飞船", "外星", "陨石", "space", "spaceship", "alien", "galaxy", "starship"), "space_night"),
    (("深海", "海洋", "海底", "水下", "潜水", "鲨鱼", "ocean", "underwater", "diver"), "ocean"),
    (("农场", "种植", "种田", "牧场", "farm", "farmer", "ranch"), "farm"),
    (("赛博", "霓虹", "黑客", "cyber", "neon", "hacker"), "neon_city"),
    (("地牢", "地下城", "迷宫", "宝箱", "dungeon"), "dungeon"),
    (("丛林", "雨林", "神庙", "藤蔓", "jungle", "temple"), "jungle"),
    (("雪原", "雪", "冰雪", "极光", "冰洞", "snow", "ice", "frozen"), "snow"),
    (("沙漠", "绿洲", "金字塔", "desert", "oasis"), "desert"),
    (("火山", "熔岩", "岩浆", "volcano", "lava", "magma"), "volcano"),
    (("蒸汽", "齿轮", "飞艇", "steampunk", "airship"), "steampunk"),
    (("中世纪", "骑士", "王国", "城堡", "medieval", "knight", "kingdom"), "medieval"),
    (("童话", "精灵", "公主", "fairy"), "fairy_tale"),
    (("石器", "原始", "恐龙", "部落", "穴居", "stone age", "dinosaur", "tribal"), "stone_age"),
    (("废土", "末日", "辐射", "荒漠废墟", "wasteland", "apocalypse"), "wasteland"),
    (("空岛", "浮空", "天空岛", "sky island", "floating island"), "sky_islands"),
    (("昆虫", "虫子", "蚂蚁", "蜜蜂", "bug", "insect", "ant"), "bug_world"),
    (("西部", "牛仔", "wild west", "cowboy"), "wild_west"),
    (("武侠", "江湖", "仙侠", "剑客", "大侠", "wuxia", "swordsman"), "wuxia"),
    (("墓地", "坟墓", "幽灵", "僵尸", "恐怖", "惊悚", "graveyard", "ghost", "zombie", "horror"), "graveyard"),
    (("校园", "学校", "学生", "教室", "campus", "school"), "campus"),
    (("拉面", "料理", "美食", "厨师", "餐厅", "ramen", "cooking", "chef"), "ramen"),
    (("埃及", "法老", "木乃伊", "egypt", "pharaoh", "mummy"), "egypt"),
    (("赛车", "竞速", "赛道", "racing", "race"), "racing_circuit"),
    (("烘焙", "面包", "蛋糕", "糕点", "bakery", "bread", "cake"), "bakery"),
    (("格斗", "竞技场", "擂台", "arena", "fighting"), "classic_arena"),
)

# 品类 → 默认主题包（主题与需求都给不出线索时的最后兜底）
_GENRE_DEFAULT_PACK: Dict[str, str] = {
    "shooter": "space_night",
    "rpg": "dungeon",
    "runner": "neon_city",
    "tower_defense": "medieval",
}


def _pack_by_id(pack_id: str) -> Optional[Dict[str, str]]:
    from src.agents.genre_fusion import THEME_PACKS

    for pack in THEME_PACKS:
        if pack["id"] == pack_id:
            return pack
    return None


def _find_pack_by_keywords(text: Optional[str]) -> Optional[Dict[str, str]]:
    """按需求文本关键词匹配主题包（首个命中优先）。"""
    if not text:
        return None
    lowered = text.lower()
    for keywords, pack_id in _KEYWORD_THEMES:
        if any(kw in lowered for kw in keywords):
            return _pack_by_id(pack_id)
    return None


def _resolve_pack(scene_ir: Any, requirements: Optional[str] = None) -> Optional[Dict[str, str]]:
    """主题包三级解析：

    1. SceneIR.theme 精确命中包 id（显式指定，最可信）
    2. 需求关键词命中（theme 常为游戏标题或歧义调色板名，关键词可纠偏）
    3. SceneIR.theme 命中 palette_base（sky_blue 等被多个包共享，歧义最低优先）
    4. 品类默认包（shooter→space_night）
    """
    theme = getattr(scene_ir, "theme", None)
    if theme:
        pack = _pack_by_id(theme)
        if pack:
            return pack
    pack = _find_pack_by_keywords(requirements)
    if pack:
        return pack
    if theme:
        from src.agents.genre_fusion import THEME_PACKS

        for p in THEME_PACKS:
            if p["palette_base"] == theme:
                return p
    genre = getattr(scene_ir, "genre", None)
    default_id = _GENRE_DEFAULT_PACK.get(genre or "")
    return _pack_by_id(default_id) if default_id else None


def _fallback_plan(scene_ir: Any, requirements: Optional[str] = None) -> Dict[str, str]:
    """母题组合模板：主题包名词/母题词填槽（零 LLM，依旧主题化）。

    槽位名词表（char/foe/item/ground...）优先，剩余槽位用母题词补位。
    """
    pack = _resolve_pack(scene_ir, requirements)
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


def plan_art(scene_ir: Any, requirements: Optional[str] = None) -> Dict[str, str]:
    """规划整份美术指导书：{素材槽位: 英文生图提示}。

    requirements 为原始需求文本：当 SceneIR.theme 无法命中主题包时
    （theme 常为游戏标题），按需求关键词匹配主题包纠偏。

    LLM 一次调用产出全部槽位（比逐槽 smart_prompt 便宜 9 倍），
    输出缺槽/解析失败 → 按槽回落母题组合模板，保证恒有可用方案。
    风格约束（星露谷像素风）不在这里写——由生图漏斗统一追加。
    """
    fallback = _fallback_plan(scene_ir, requirements)
    if os.getenv("GAMEFORGE_SMART_PROMPTS", "1").strip().lower() in {"0", "false", "no"}:
        return fallback

    pack = _resolve_pack(scene_ir, requirements)
    motifs = pack["motifs"] if pack else "cozy village"
    genre = getattr(scene_ir, "genre", "platformer")
    try:
        from src.utils.llm_client import get_llm_client

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
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
            max_tokens=2000,
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
