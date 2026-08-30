"""GameForge - 游戏概念组合引擎

思路：不硬写 100 个品类规格，而是用「主流基款 × 玩法变体牌 × 主题包」
的确定性组合排列，把 20 个基款分散出 15000+ 种游戏概念，
其中筛选验证出 **100 个备用方案**（CONCEPT_LIBRARY，种子固定、跨运行稳定），
并支持随机摇取（灵感骰子）实现自由创作。

组合空间：20 基款(含自身融合) × 28 变体牌 × 25 主题包 ≈ 15,680
融合方式：主基款定核心循环/镜头/胜负框架，副基款贡献 1-2 条招牌机制，
         变体牌追加机制与拓展方向，主题包决定美术调性。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.agents.genre_specs import GENRE_SPECS, GenreSpec, UNIVERSAL_BASELINE, infer_difficulty

# ═══════════════════════════════════════════════════════════════
# 玩法变体牌（28 张）—— 叠在任何基款上改变玩法记忆点
# ═══════════════════════════════════════════════════════════════

TWIST_CARDS: List[Dict[str, str]] = [
    {"id": "roguelike_runs", "name_zh": "局内随机化", "mech": "每局地图/掉落随机生成，死亡重开不重复"},
    {"id": "boss_rush", "name_zh": "Boss 连战", "mech": "每关结尾一场机制各异的 Boss 战"},
    {"id": "time_attack", "name_zh": "限时挑战", "mech": "全流程倒计时压迫，时间也是资源"},
    {"id": "speedrun", "name_zh": "速通计时", "mech": "分段计时与最速纪录驱动重复游玩"},
    {"id": "endless", "name_zh": "无尽模式", "mech": "关卡无限生成，难度缓慢爬升"},
    {"id": "two_player", "name_zh": "本地双人", "mech": "同屏双人分摊操作与配合"},
    {"id": "co_op_ai", "name_zh": "AI 搭档", "mech": "一名可指挥的 AI 伙伴协同作战"},
    {"id": "gravity_flip", "name_zh": "重力反转", "mech": "一键翻转重力，天地双面行走"},
    {"id": "reverse_controls", "name_zh": "反向操作", "mech": "周期性左右反向，考验肌肉记忆"},
    {"id": "day_night", "name_zh": "昼夜循环", "mech": "昼夜切换改变敌人构成与视野"},
    {"id": "weather", "name_zh": "动态天气", "mech": "雨雪风影响物理与视野"},
    {"id": "crafting", "name_zh": "素材合成", "mech": "收集素材合成道具与装备"},
    {"id": "economy", "name_zh": "经济系统", "mech": "买卖价格波动，商店策略"},
    {"id": "stealth", "name_zh": "潜行窃取", "mech": "规避视线偷取目标，被发现即失败代价"},
    {"id": "clone_mayhem", "name_zh": "分身乱舞", "mech": "操作分身同时行动解谜/作战"},
    {"id": "magnet", "name_zh": "磁力吸附", "mech": "磁力吸引道具与攀附金属面"},
    {"id": "shrink", "name_zh": "缩小通行", "mech": "缩小身体钻入狭缝改变通路"},
    {"id": "card_draw", "name_zh": "卡牌技能", "mech": "技能以卡牌抽取，构筑手牌流"},
    {"id": "fusion_evolve", "name_zh": "合体进化", "mech": "同类元素合体升级成高阶形态"},
    {"id": "ghost_race", "name_zh": "幽灵竞速", "mech": "与上局自己的影子录像赛跑"},
    {"id": "level_editor", "name_zh": "关卡编辑器", "mech": "内置编辑器自制关卡分享"},
    {"id": "daily_seed", "name_zh": "每日种子", "mech": "全玩家同一种子，比拼每日榜"},
    {"id": "pacifist", "name_zh": "和平主义", "mech": "全程不可击杀敌人，纯躲避解法通关"},
    {"id": "gravity_well", "name_zh": "引力井", "mech": "场景引力点改变弹道与轨迹"},
    {"id": "beat_sync", "name_zh": "节拍同步", "mech": "场景随 BGM 节拍脉动，卡点有加成"},
    {"id": "hot_potato", "name_zh": "烫手山芋", "mech": "危险物限时倒手，脱手即引爆"},
    {"id": "fog_of_war", "name_zh": "战争迷雾", "mech": "视野半径外全黑，探索即风险"},
    {"id": "boss_enrage", "name_zh": "狂暴递增", "mech": "拖延越久敌人越狂暴，鼓励速攻"},
]

# ═══════════════════════════════════════════════════════════════
# 主题包（25 个）—— 决定美术调性（palette_base 对应场景调色板）
# ═══════════════════════════════════════════════════════════════

THEME_PACKS: List[Dict[str, str]] = [
    {"id": "farm", "name_zh": "星露谷农场", "palette_base": "forest_green", "motifs": "农场 谷仓 风车 作物"},
    {"id": "space_night", "name_zh": "深空舰桥", "palette_base": "space_black", "motifs": "星舰 星云 小行星"},
    {"id": "neon_city", "name_zh": "赛博霓虹", "palette_base": "neon_purple", "motifs": "霓虹 雨夜 全息"},
    {"id": "dungeon", "name_zh": "火把地牢", "palette_base": "lava_red", "motifs": "地牢 石墙 宝箱"},
    {"id": "ocean", "name_zh": "深海遗迹", "palette_base": "sky_blue", "motifs": "深海 珊瑚 沉船"},
    {"id": "jungle", "name_zh": "丛林神庙", "palette_base": "forest_green", "motifs": "丛林 藤蔓 神庙"},
    {"id": "snow", "name_zh": "雪原极光", "palette_base": "sky_blue", "motifs": "雪原 极光 冰洞"},
    {"id": "desert", "name_zh": "大漠孤烟", "palette_base": "warm_beige", "motifs": "沙漠 绿洲 金字塔"},
    {"id": "volcano", "name_zh": "熔岩火山", "palette_base": "lava_red", "motifs": "熔岩 灰烬 火山口"},
    {"id": "steampunk", "name_zh": "蒸汽朋克", "palette_base": "warm_beige", "motifs": "齿轮 蒸汽 飞艇"},
    {"id": "medieval", "name_zh": "中世纪王国", "palette_base": "forest_green", "motifs": "城堡 骑士 旗帜"},
    {"id": "fairy_tale", "name_zh": "糖果童话", "palette_base": "sky_blue", "motifs": "糖果 独角兽 城堡"},
    {"id": "stone_age", "name_zh": "石器部落", "palette_base": "warm_beige", "motifs": "石斧 猛犸 图腾"},
    {"id": "wasteland", "name_zh": "末日废土", "palette_base": "lava_red", "motifs": "废墟 辐射 沙暴"},
    {"id": "sky_islands", "name_zh": "天空群岛", "palette_base": "sky_blue", "motifs": "浮岛 云海 飞艇"},
    {"id": "bug_world", "name_zh": "微缩昆虫", "palette_base": "forest_green", "motifs": "昆虫 蘑菇 露珠"},
    {"id": "wild_west", "name_zh": "狂野西部", "palette_base": "warm_beige", "motifs": "牛仔 沙镇 仙人掌"},
    {"id": "wuxia", "name_zh": "水墨武侠", "palette_base": "forest_green", "motifs": "竹林 轻剑 客栈"},
    {"id": "graveyard", "name_zh": "月光墓地", "palette_base": "neon_purple", "motifs": "墓碑 月光 乌鸦"},
    {"id": "campus", "name_zh": "像素校园", "palette_base": "sky_blue", "motifs": "教室 操场 社团"},
    {"id": "ramen", "name_zh": "深夜拉面店", "palette_base": "warm_beige", "motifs": "拉面 灯笼 灶台"},
    {"id": "egypt", "name_zh": "古埃及", "palette_base": "warm_beige", "motifs": "法老 木乃伊 金字塔"},
    {"id": "racing_circuit", "name_zh": "霓虹赛道", "palette_base": "neon_purple", "motifs": "赛道 赛车 计时牌"},
    {"id": "bakery", "name_zh": "面包房物语", "palette_base": "warm_beige", "motifs": "面包 烤箱 围裙"},
    {"id": "classic_arena", "name_zh": "复古竞技场", "palette_base": "default", "motifs": "黑白像素 网格"},
]


# ═══════════════════════════════════════════════════════════════
# 概念（融合规格）与生成器
# ═══════════════════════════════════════════════════════════════

@dataclass
class GameConcept:
    """一个可执行的游戏概念 = 基款融合 + 变体牌 + 主题包"""

    spec: GenreSpec                       # 融合后的完整规格（可直接进现有管线）
    theme_pack: Dict[str, str]            # 主题包
    twist: Dict[str, str]                 # 变体牌
    pitch: str                            # 一句话卖点
    primary_id: str = ""
    secondary_id: str = ""

    @property
    def genre_id(self) -> str:
        return self.spec.id


def fuse_genre(
    primary_id: str,
    secondary_id: str,
    twist_id: Optional[str] = None,
    theme_pack: Optional[Dict[str, str]] = None,
) -> GenreSpec:
    """融合两个基款 + 变体牌 → 新 GenreSpec。

    主基款决定核心循环/镜头/胜负框架与场景蓝图主体；
    副基款贡献 1-2 条招牌机制与一个签名实体；
    变体牌追加 1 条机制与 1 条拓展方向。
    """
    a = GENRE_SPECS[primary_id]
    b = GENRE_SPECS[secondary_id]
    twist = next((t for t in TWIST_CARDS if t["id"] == twist_id), None)

    fused_id = a.id if a.id == b.id else f"{a.id}_{b.id}"
    mechanics = list(a.mechanics[:3])
    borrowed = [m for m in b.mechanics if m not in mechanics][:2]
    mechanics += borrowed
    if twist:
        mechanics.append(twist["mech"])

    extensions = list(a.extensions[:3])
    extensions += [e for e in b.extensions if e not in extensions][:1]
    if twist:
        extensions.append(f"把「{twist['name_zh']}」做成核心记忆点")

    entities = [dict(e) for e in a.entities]
    # 副基款签名实体（敌人/道具/NPC 角色优先，最多带 1 个，避免蓝图膨胀）
    if a.id != b.id:
        for e in b.entities:
            if e["role"] in ("enemy", "pickup", "npc") and all(x["role"] != e["role"] for x in entities):
                new_e = dict(e)
                new_e["name"] = f"{b.id.capitalize()}{e['name']}"
                entities.append(new_e)
                break

    return GenreSpec(
        id=fused_id,
        name_zh=f"{a.name_zh}×{b.name_zh}" if a.id != b.id else f"{a.name_zh}·{twist['name_zh'] if twist else '变体'}",
        representative=f"{a.representative} × {b.representative}" if a.id != b.id else a.representative,
        core_loop=f"{a.core_loop}（融合 {b.name_zh} 的元素）",
        mechanics=mechanics,
        extensions=extensions,
        entities=entities,
        win_condition=a.win_condition,
        lose_condition=a.lose_condition,
        keywords=list(a.keywords),
        camera=a.camera,
        hud_extras=list(dict.fromkeys(a.hud_extras + b.hud_extras))[:4],
        theme=(theme_pack or {}).get("palette_base", a.theme),
    )


def _concept_pitch(a: GenreSpec, b: GenreSpec, twist: Optional[Dict[str, str]],
                   theme_pack: Dict[str, str]) -> str:
    borrowed = b.mechanics[0] if a.id != b.id else a.mechanics[0]
    pitch = f"【{theme_pack['name_zh']}】{a.name_zh}核心循环，融入{b.name_zh}的「{borrowed}」"
    if twist:
        pitch += f"，加上「{twist['name_zh']}」变体"
    return pitch


def roll_concept(seed: Optional[int] = None) -> GameConcept:
    """摇一个游戏概念（灵感骰子）。seed 固定则结果可复现。"""
    rng = random.Random(seed)
    primary = rng.choice(list(GENRE_SPECS.keys()))
    secondary = rng.choice(list(GENRE_SPECS.keys()))
    twist = rng.choice(TWIST_CARDS)
    theme_pack = rng.choice(THEME_PACKS)
    spec = fuse_genre(primary, secondary, twist["id"], theme_pack)
    a, b = GENRE_SPECS[primary], GENRE_SPECS[secondary]
    return GameConcept(
        spec=spec,
        theme_pack=theme_pack,
        twist=twist,
        pitch=_concept_pitch(a, b, twist, theme_pack),
        primary_id=primary,
        secondary_id=secondary,
    )


def build_concept_library(count: int = 100, seed: int = 42) -> List[GameConcept]:
    """确定性构建 ≥count 个不重复的备用游戏概念（种子固定 → 跨运行稳定）。"""
    rng = random.Random(seed)
    seen: set = set()
    library: List[GameConcept] = []
    genre_ids = list(GENRE_SPECS.keys())
    attempts = 0
    while len(library) < count and attempts < count * 40:
        attempts += 1
        primary = rng.choice(genre_ids)
        secondary = rng.choice(genre_ids)
        twist = rng.choice(TWIST_CARDS)
        theme_pack = rng.choice(THEME_PACKS)
        key = (primary, secondary, twist["id"], theme_pack["id"])
        if key in seen:
            continue
        seen.add(key)
        spec = fuse_genre(primary, secondary, twist["id"], theme_pack)
        a, b = GENRE_SPECS[primary], GENRE_SPECS[secondary]
        library.append(GameConcept(
            spec=spec,
            theme_pack=theme_pack,
            twist=twist,
            pitch=_concept_pitch(a, b, twist, theme_pack),
            primary_id=primary,
            secondary_id=secondary,
        ))
    return library


def build_concept_prompt_hint(concept: GameConcept, difficulty: Optional[str] = None) -> str:
    """把融合概念变成注入 planner / code_generator 的规格提示。"""
    spec = concept.spec
    difficulty = difficulty or "medium"
    lines = [
        f"[游戏概念] {concept.pitch}",
        f"[融合基款] {spec.representative}",
        f"[难度档位] {difficulty} → "
        + ("直接生成简易可玩成品（MVP）" if difficulty == "easy"
           else "分期生成：第一期 MVP，第二期增强" if difficulty == "hard"
           else "标准版"),
        "必须实现的核心机制：",
    ]
    lines += [f"- {m}" for m in spec.mechanics]
    lines.append("基本功能基线（缺一不可）：")
    lines += [f"- {f}" for f in UNIVERSAL_BASELINE]
    if spec.extensions:
        lines.append("本作选定拓展方向：")
        lines += [f"- {e}" for e in spec.extensions[:2]]
    lines.append(f"主题美术方向：{concept.theme_pack['name_zh']}（{concept.theme_pack['motifs']}）")
    lines.append(f"胜利条件：{spec.win_condition}")
    lines.append(f"失败条件：{spec.lose_condition}")
    return "\n".join(lines)
