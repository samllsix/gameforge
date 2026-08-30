"""GameForge - 游戏品类规格库

把市场上能见到的游戏品类各挑一款代表作作为基款，定义：
- 通用基线（任何游戏都必须有的基本功能，对齐常规游戏开发流程）
- 品类核心机制（基款游戏的招牌玩法）
- 可选拓展（从基款分散拓展的功能方向，生成时按需挑选）
- 场景蓝图（实体构成，直接喂给场景生成器）
- 关键词（需求 → 品类的智能匹配）

使用方式：
    from src.agents.genre_specs import match_genre, build_genre_prompt_hint
    match = match_genre("做一个像马里奥那样的跳台游戏")
    hint = build_genre_prompt_hint(match)   # 拼进 planner/codegen 的 LLM prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════
# 通用基线：常规游戏开发流程中任何游戏都必须有的基本功能
# （生成的任务计划与验收清单都以此为底线）
# ═══════════════════════════════════════════════════════════════

UNIVERSAL_BASELINE: List[str] = [
    "玩家输入控制（键盘移动与核心动作）",
    "玩家控制器与物理（移动/重力/碰撞体）",
    "碰撞检测与响应",
    "计分系统（得分/收集计数）",
    "HUD 显示（分数/计时，品类附加项见 hud_extras）",
    "明确的胜利条件",
    "明确的失败条件",
    "游戏结束画面与重新开始",
    "暂停与恢复",
    "难度随进度递增",
]


@dataclass(frozen=True)
class GenreSpec:
    """单款品类规格（以一款代表作作为基款）"""

    id: str                                  # 品类 id（SceneIR.genre 合法值）
    name_zh: str                             # 品类中文名
    representative: str                      # 代表作（基款）
    core_loop: str                           # 一句话核心循环
    mechanics: List[str]                     # 品类核心机制（基款招牌玩法，必须实现）
    extensions: List[str]                    # 可选拓展方向（分散拓展，按需求挑选 2-3 个）
    entities: List[Dict[str, str]]           # 场景蓝图实体（name/role/count/spawn_zone）
    win_condition: str
    lose_condition: str
    keywords: List[str]                      # 需求匹配关键词（中英文）
    camera: str = "2d_side_view"             # 2d_side_view / 2d_top_down
    hud_extras: List[str] = field(default_factory=list)
    theme: str = "sky_blue"

    @property
    def baseline(self) -> List[str]:
        """完整基线 = 通用基线 + 品类核心机制"""
        return UNIVERSAL_BASELINE + self.mechanics


# 实体蓝图速写助手
def _e(name: str, role: str, count: int = 1, zone: str = "center") -> Dict[str, str]:
    return {"name": name, "role": role, "count": str(count), "spawn_zone": zone}


# ═══════════════════════════════════════════════════════════════
# 品类注册表：市场上主流品类各挑一款代表作作为基款
# ═══════════════════════════════════════════════════════════════

GENRE_SPECS: Dict[str, GenreSpec] = {
    spec.id: spec
    for spec in [
        GenreSpec(
            id="platformer",
            name_zh="横版平台跳跃",
            representative="超级马里奥兄弟 (Super Mario Bros.)",
            core_loop="跑跳穿越关卡 → 踩敌/吃金币得分 → 抵达终点旗杆过关",
            mechanics=[
                "精准跳跃（长按跳更高、土狼时间宽容）",
                "踩踏消灭敌人",
                "可收集金币与道具方块",
                "横向卷轴关卡与终点目标",
            ],
            extensions=[
                "二段跳/墙跳",
                "移动平台与消失平台",
                "检查点与生命系统",
                "顶头砖块弹出道具",
                "Boss 关卡",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Platform", "platform", 3, "center"),
                _e("Coin", "pickup", 3, "center"),
                _e("Goomba", "enemy", 2, "right"),
                _e("Tree", "decoration", 2, "left"),
            ],
            win_condition="抵达关卡终点旗杆",
            lose_condition="碰到敌人或掉出屏幕底部，生命耗尽",
            keywords=["马里奥", "平台跳跃", "跳台", "platformer", "mario", "跳跃", "横版", "闯关"],
            hud_extras=["生命值"],
        ),
        GenreSpec(
            id="shooter",
            name_zh="太空射击",
            representative="太空侵略者 (Space Invaders)",
            core_loop="移动飞船 → 射击成片敌阵 → 敌阵逐波下压 → 消灭全部敌人进入下一波",
            mechanics=[
                "水平移动 + 按键射击",
                "成队列编队下压的敌人波次",
                "敌方随机开火",
                "波次清空后进入更难的一波",
            ],
            extensions=[
                "掩体掩体耐久",
                "武器升级（双发/散射）",
                "UFO 随机高分目标",
                "Boss 波",
            ],
            entities=[
                _e("Player", "player", 1, "bottom"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Invader", "enemy", 4, "center"),
                _e("Powerup", "pickup", 1, "center"),
            ],
            win_condition="清空当前波次全部敌人",
            lose_condition="敌人抵达底线或生命耗尽",
            keywords=["射击", "太空", "侵略者", "shooter", "shump", "invaders", "子弹", "弹幕", "打飞机"],
            hud_extras=["生命值", "波次"],
            theme="space_night",
        ),
        GenreSpec(
            id="runner",
            name_zh="跑酷",
            representative="地铁跑酷 (Subway Surfers)",
            core_loop="角色自动向前跑 → 跳跃/滑铲躲障碍 → 收集金币 → 越跑越快直到撞上障碍",
            mechanics=[
                "角色自动前进，玩家只控制跳跃/滑铲",
                "障碍流水线生成与回收",
                "速度随时间提升",
                "金币串收集",
            ],
            extensions=[
                "磁铁/加速道具",
                "双段跳跃",
                "场景切换（白天/夜晚）",
                "每日挑战任务",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Platform", "platform", 3, "center"),
                _e("Coin", "pickup", 4, "center"),
                _e("Barrier", "enemy", 2, "right"),
            ],
            win_condition="无尽模式：以距离/分数为目标（达到目标距离即胜利）",
            lose_condition="撞上障碍物",
            keywords=["跑酷", "runner", " Subway", "地铁跑酷", "无尽跑", "temple run", "auto runner"],
            hud_extras=["距离"],
        ),
        GenreSpec(
            id="puzzle",
            name_zh="方块益智",
            representative="俄罗斯方块 (Tetris)",
            core_loop="下落方块 → 旋转平移拼满整行 → 满行消除得分 → 堆到顶部即结束",
            mechanics=[
                "七种方块随机下落",
                "旋转与左右移动",
                "满行消除与连击计分",
                "堆叠高度即失败线",
            ],
            extensions=[
                "下一块预览",
                "硬降（空格直接落底）",
                "消行特效与连击加成",
                "无尽加速",
            ],
            entities=[
                _e("Player", "player", 1, "center"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Wall", "boundary", 2, "center"),
            ],
            win_condition="无尽模式：达到目标分数即胜利",
            lose_condition="方块堆叠到场地顶部",
            keywords=["俄罗斯方块", "tetris", "方块", "消除", "益智", "puzzle", "拼图"],
            camera="2d_top_down",
            theme="neon",
        ),
        GenreSpec(
            id="snake",
            name_zh="贪吃蛇",
            representative="贪吃蛇 (Snake)",
            core_loop="控制蛇头方向 → 吃食物变长 → 蛇身越来越长 → 撞墙或撞自己即结束",
            mechanics=[
                "网格移动与转向（不能直接掉头）",
                "吃食物身体加长",
                "蛇身碰撞判定",
                "随长度提升的速度",
            ],
            extensions=[
                "特殊食物（限时/高分）",
                "障碍物关卡",
                "穿墙模式",
                "双人对战",
            ],
            entities=[
                _e("Player", "player", 1, "center"),
                _e("Food", "pickup", 3, "center"),
                _e("Wall", "boundary", 4, "center"),
            ],
            win_condition="无尽模式：达到目标长度/分数即胜利",
            lose_condition="撞墙或咬到自己",
            keywords=["贪吃蛇", "snake", "蛇", "吃食物"],
            camera="2d_top_down",
            theme="grid_green",
        ),
        GenreSpec(
            id="breakout",
            name_zh="打砖块",
            representative="打砖块 (Breakout / Arkanoid)",
            core_loop="移动挡板 → 弹球撞击砖块 → 砖块全消进入下一关 → 球落地损失生命",
            mechanics=[
                "挡板反弹角度控制",
                "球与砖块碰撞消除",
                "砖块阵型逐关变化",
                "球速随时间加快",
            ],
            extensions=[
                "道具掉落（加长挡板/多球）",
                "不可摧毁砖块",
                "Boss 砖块",
            ],
            entities=[
                _e("Player", "player", 1, "bottom"),
                _e("Wall", "boundary", 3, "center"),
                _e("Brick", "platform", 3, "center"),
            ],
            win_condition="清空全部砖块",
            lose_condition="球落地且生命耗尽",
            keywords=["打砖块", "breakout", "arkanoid", "弹球", "砖块"],
            hud_extras=["生命值"],
            theme="neon",
        ),
        GenreSpec(
            id="flappy",
            name_zh="飞行躲避",
            representative="Flappy Bird",
            core_loop="点击扇翅膀上升 → 重力下拉 → 穿过管道缝隙 → 每过一根管道得分",
            mechanics=[
                "单键点击上升 + 持续重力",
                "管道缝隙障碍流水线",
                "穿缝计分",
                "一触即死",
            ],
            extensions=[
                "日夜交替背景",
                "可收集星星",
                "逐渐收窄的缝隙",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Pipe", "platform", 4, "center"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Star", "pickup", 2, "center"),
            ],
            win_condition="无尽模式：达到目标分数即胜利",
            lose_condition="碰到管道或地面",
            keywords=["flappy", "小鸟", "飞行", "管道", "点击", "躲避"],
            theme="day_sky",
        ),
        GenreSpec(
            id="tower_defense",
            name_zh="塔防",
            representative="植物大战僵尸 (Plants vs. Zombies)",
            core_loop="沿路径布置防御塔 → 消耗资源建造升级 → 阻击一波波进攻的敌人 → 撑过全部波次",
            mechanics=[
                "固定路径行进的敌人波次",
                "可建造的防御塔（消耗资源/冷却）",
                "资源生产循环",
                "波次间隔与难度递增",
            ],
            extensions=[
                "多种塔类型（减速/溅射/远程）",
                "塔升级与出售",
                "Boss 波",
                "多路径地图",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("TowerSpot", "platform", 3, "center"),
                _e("Zombie", "enemy", 3, "right"),
                _e("Sun", "pickup", 2, "center"),
            ],
            win_condition="撑过全部波次",
            lose_condition="敌人抵达基地（屏幕左侧）",
            keywords=["塔防", "tower defense", "植物大战僵尸", "保卫", "波次", "防御塔"],
            camera="2d_top_down",
            hud_extras=["资源量", "波次"],
            theme="garden",
        ),
        GenreSpec(
            id="rpg",
            name_zh="顶视冒险 RPG",
            representative="塞尔达传说 (The Legend of Zelda)",
            core_loop="顶视地图探索 → 与 NPC 对话接任务 → 战斗获得资源 → 解锁新区域推进剧情",
            mechanics=[
                "八方向顶视移动",
                "近战攻击与敌人 AI",
                "NPC 对话与任务",
                "区域/房间切换",
            ],
            extensions=[
                "装备与背包",
                "宝箱与钥匙门锁",
                "血量与药水",
                "Boss 战",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("NPC", "npc", 2, "center"),
                _e("Slime", "enemy", 2, "right"),
                _e("Tree", "decoration", 3, "center"),
                _e("Heart", "pickup", 2, "center"),
            ],
            win_condition="击败区域 Boss / 完成任务目标",
            lose_condition="生命值耗尽",
            keywords=["rpg", "塞尔达", "冒险", "探险", "顶视", "zelda", "角色扮演", "任务", "剧情"],
            camera="2d_top_down",
            hud_extras=["生命值"],
            theme="forest",
        ),
        GenreSpec(
            id="farming_sim",
            name_zh="农场经营模拟",
            representative="星露谷物语 (Stardew Valley)",
            core_loop="开垦播种 → 浇水等待生长 → 收获出售赚钱 → 扩张农场与升级工具",
            mechanics=[
                "日历与体力系统",
                "耕种-浇水-收获循环",
                "金币经济与商店",
                "NPC 好感度",
            ],
            extensions=[
                "季节与作物表",
                "畜牧",
                "钓鱼小游戏",
                "矿洞探险",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Farmland", "ground", 1, "bottom"),
                _e("Crop", "pickup", 4, "center"),
                _e("Villager", "npc", 2, "center"),
                _e("Barn", "decoration", 1, "right"),
                _e("Tree", "decoration", 2, "left"),
            ],
            win_condition="达到目标金币/农场等级",
            lose_condition="（经营类无失败）体力耗尽即休息进入次日",
            keywords=["星露谷", "stardew", "农场", "种田", "经营", "模拟", "farming", "sim"],
            camera="2d_top_down",
            hud_extras=["金币", "体力", "日期"],
            theme="farm",
        ),
        GenreSpec(
            id="survivors",
            name_zh="割草生存",
            representative="吸血鬼幸存者 (Vampire Survivors)",
            core_loop="移动走位 → 武器自动攻击围上来的怪群 → 吃经验宝石升级 → 选新武器组成 Build 活到时限",
            mechanics=[
                "大量敌人持续围攻刷出",
                "武器自动发射，玩家只走位",
                "经验宝石掉落与升级三选一",
                "时限决胜（30 分钟怪潮）",
            ],
            extensions=[
                "武器进化合成",
                "被动道具 Build",
                "精英怪与宝箱",
                "多地图",
            ],
            entities=[
                _e("Player", "player", 1, "center"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Bat", "enemy", 4, "center"),
                _e("Gem", "pickup", 3, "center"),
            ],
            win_condition="存活到时限 / 击败最终 Boss",
            lose_condition="生命值耗尽",
            keywords=["幸存者", "survivors", "割草", "吸血鬼", "roguelite", "弹幕生存"],
            camera="2d_top_down",
            hud_extras=["生命值", "经验条", "击杀数"],
            theme="dark_graveyard",
        ),
        GenreSpec(
            id="racing",
            name_zh="俯视竞速",
            representative="马里奥赛车 (Mario Kart)",
            core_loop="绕圈驾驶 → 漂移过弯抢位 → 拾取道具干扰对手 → 最先冲线获胜",
            mechanics=[
                "加速/转向/漂移驾驶手感",
                "圈数与计时",
                "赛道道具箱",
                "AI 对手寻路",
            ],
            extensions=[
                "道具（香蕉皮/加速垫）",
                "迷你地图",
                "多赛道锦标赛",
                "车辆改装",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Track", "ground", 1, "bottom"),
                _e("Rival", "enemy", 2, "center"),
                _e("ItemBox", "pickup", 3, "center"),
                _e("Wall", "boundary", 4, "center"),
            ],
            win_condition="率先完成指定圈数",
            lose_condition="（竞速无失败）用时排名决定成绩",
            keywords=["赛车", "竞速", "racing", "kart", "马里奥赛车", "漂移", "开车"],
            camera="2d_top_down",
            hud_extras=["圈数", "排名"],
            theme="circuit",
        ),
        GenreSpec(
            id="match3",
            name_zh="三消",
            representative="糖果传奇 (Candy Crush Saga)",
            core_loop="交换相邻元素 → 凑成三连消除 → 连锁反应刷分 → 限定步数内达标过关",
            mechanics=[
                "相邻交换触发三连消除",
                "连锁反应与连击计分",
                "关卡目标分数与限定步数",
                "四连/五连生成特殊元素",
            ],
            extensions=[
                "炸弹糖果（范围消除）",
                "冰块/果冻障碍层",
                "无尽模式",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Gem", "pickup", 4, "center"),
            ],
            win_condition="限定步数内达到目标分数",
            lose_condition="步数用尽未达标",
            keywords=["三消", "糖果", "消除", "match3", "candy", "宝石"],
            camera="2d_top_down",
            hud_extras=["剩余步数"],
            theme="candy",
        ),
        GenreSpec(
            id="sokoban",
            name_zh="推箱子",
            representative="推箱子 (Sokoban)",
            core_loop="把箱子推到目标点 → 箱子只能推不能拉 → 全部归位过关",
            mechanics=[
                "网格推动物理（只能推不能拉）",
                "箱子与目标点匹配判定",
                "墙体与死角（推进角落即卡死）",
                "逐步关卡推进",
            ],
            extensions=[
                "撤销一步（Undo）",
                "步数最优化评分",
                "多箱子递进关卡",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Crate", "pickup", 3, "center"),
                _e("Wall", "boundary", 4, "center"),
            ],
            win_condition="全部箱子推到目标点",
            lose_condition="箱子推进死角无法完成",
            keywords=["推箱子", "sokoban", "仓库番", "箱子"],
            camera="2d_top_down",
            hud_extras=["步数"],
            theme="warehouse",
        ),
        GenreSpec(
            id="minesweeper",
            name_zh="扫雷",
            representative="扫雷 (Minesweeper)",
            core_loop="翻开格子 → 数字提示周围雷数 → 推理排雷 → 标出全部地雷即胜利",
            mechanics=[
                "雷区随机布雷",
                "数字提示周围雷数",
                "空白格连锁展开",
                "右键插旗标记",
            ],
            extensions=[
                "首点保证安全（首次点击不炸）",
                "计时排行榜",
                "自定义雷区尺寸与雷数",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Wall", "boundary", 4, "center"),
            ],
            win_condition="翻开所有非雷格子",
            lose_condition="踩到地雷",
            keywords=["扫雷", "minesweeper", "地雷", "雷区"],
            camera="2d_top_down",
            hud_extras=["剩余雷数", "计时"],
            theme="grid_gray",
        ),
        GenreSpec(
            id="merge_2048",
            name_zh="数字合成",
            representative="2048",
            core_loop="四方向滑动 → 相同数字合并翻倍 → 合成 2048 即胜利 → 棋盘塞满无法合并即结束",
            mechanics=[
                "四方向滑动全部方块",
                "相同数字合并翻倍",
                "每步随机生成新方块",
                "可动性判定（无路可走即结束）",
            ],
            extensions=[
                "撤销一步",
                "超越 2048 挑战（4096/8192）",
                "滑动动画与分数动效",
            ],
            entities=[
                _e("Player", "player", 1, "center"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Wall", "boundary", 4, "center"),
            ],
            win_condition="合成出 2048 方块",
            lose_condition="棋盘填满且无可合并项",
            keywords=["2048", "合成", "数字", "merge"],
            camera="2d_top_down",
            theme="grid_amber",
        ),
        GenreSpec(
            id="pong",
            name_zh="乒乓对战",
            representative="Pong",
            core_loop="移动挡板 → 击球反弹 → 球落界对手得分 → 先到目标分者胜",
            mechanics=[
                "挡板上下移动",
                "反弹角度随击中位置变化",
                "AI 对手跟踪追球",
                "得分与发球轮换",
            ],
            extensions=[
                "球速逐渐加快",
                "双人本地对战",
                "场地障碍",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Rival", "enemy", 1, "right"),
                _e("Wall", "boundary", 3, "center"),
            ],
            win_condition="先得到目标分数（如 7 分）",
            lose_condition="对手先达到目标分数",
            keywords=["pong", "乒乓", "弹球对战", "挡板", "对战"],
            camera="2d_top_down",
            hud_extras=["双方比分"],
            theme="retro_black",
        ),
        GenreSpec(
            id="rhythm",
            name_zh="音乐节奏",
            representative="节奏大师 / osu!",
            core_loop="音符沿轨道下落 → 按键精准击打 → 判定 Perfect/Good/Miss → 连击刷分通关",
            mechanics=[
                "音符按轨道定时下落",
                "按键时机判定（Perfect/Good/Miss）",
                "连击 Combo 系统",
                "谱面随 BPM 生成",
            ],
            extensions=[
                "多难度谱面",
                "长按与连击音符",
                "自定义音乐导入",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Note", "pickup", 4, "center"),
            ],
            win_condition="歌曲结束达到目标准确率",
            lose_condition="生命值（连续 Miss）耗尽",
            keywords=["节奏", "音游", "rhythm", "音乐游戏", "osu", "下落式"],
            camera="2d_top_down",
            hud_extras=["连击数", "准确率"],
            theme="neon",
        ),
        GenreSpec(
            id="clicker",
            name_zh="放置点击",
            representative="Cookie Clicker",
            core_loop="点击产出资源 → 购买自动化建筑 → 产量指数增长 → 解锁更高阶内容",
            mechanics=[
                "点击主产出循环",
                "建筑购买与自动化产出",
                "指数增长与价格通胀",
                "离线收益结算",
            ],
            extensions=[
                "成就系统",
                "转生（Prestige）永久加成",
                "点击暴击",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
                _e("Cookie", "pickup", 2, "center"),
                _e("Bakery", "decoration", 1, "right"),
            ],
            win_condition="达到目标产量/解锁全部建筑",
            lose_condition="（放置类无失败）",
            keywords=["点击", "放置", "挂机", "clicker", "idle", "增量"],
            camera="2d_top_down",
            hud_extras=["每秒产量"],
            theme="bakery",
        ),
        GenreSpec(
            id="word_guess",
            name_zh="文字猜词",
            representative="Wordle",
            core_loop="限定次数内猜词 → 每次给出字母位置反馈（绿/黄/灰） → 用推理缩小范围猜中即胜",
            mechanics=[
                "限定次数猜词",
                "字母存在性与位置反馈",
                "虚拟键盘与已用字母标记",
                "每日一词（可随机词库）",
            ],
            extensions=[
                "中文成语模式",
                "计时挑战模式",
                "战绩统计",
            ],
            entities=[
                _e("Player", "player", 1, "left"),
                _e("Ground", "ground", 1, "bottom"),
            ],
            win_condition="在限定次数内猜中单词",
            lose_condition="次数用尽未猜中",
            keywords=["wordle", "猜词", "猜单词", "文字游戏", "成语接龙"],
            camera="2d_top_down",
            hud_extras=["剩余次数"],
            theme="paper",
        ),
    ]
}

# SceneIR.genre 合法值同步：这些 id 必须被 scene_ir 接受
SUPPORTED_GENRE_IDS = list(GENRE_SPECS.keys())


# ═══════════════════════════════════════════════════════════════
# 智能匹配：需求文本 → 品类（关键词计分，零 LLM、确定性好）
# ═══════════════════════════════════════════════════════════════

def match_genre(requirements: str) -> Optional[GenreSpec]:
    """从需求文本匹配最合适的品类规格；无法识别返回 None（由调用方走默认 platformer）。"""
    if not requirements:
        return None
    text = requirements.lower()
    best: Optional[GenreSpec] = None
    best_score = 0
    for spec in GENRE_SPECS.values():
        score = sum(1 for kw in spec.keywords if kw.lower() in text)
        # 代表作名直接出现是强信号
        if spec.representative.split(" ")[0].lower() in text:
            score += 3
        if score > best_score:
            best, best_score = spec, score
    return best


def get_spec(genre_id: Optional[str]) -> GenreSpec:
    """按 id 取规格；未知 id 回退 platformer（与 SceneIR 校验回退一致）。"""
    if genre_id and genre_id in GENRE_SPECS:
        return GENRE_SPECS[genre_id]
    return GENRE_SPECS["platformer"]


# ═══════════════════════════════════════════════════════════════
# 难度分级：按需求难易度间接（分期）或直接生成简易版成品
# ═══════════════════════════════════════════════════════════════

_EASY_KEYWORDS = ["简单", "简易", "快速", "试玩", "demo", "入门", "最小", "极简", "小游戏", "原型", "easy", "simple"]
_HARD_KEYWORDS = ["困难", "复杂", "完整", "挑战", "boss", "大型", "高难度", "进阶", "丰富", "hard", "高级"]


def infer_difficulty(requirements: str) -> str:
    """从需求文本推断难度档位：easy / medium / hard。"""
    text = (requirements or "").lower()
    if any(k in text for k in _EASY_KEYWORDS):
        return "easy"
    if any(k in text for k in _HARD_KEYWORDS):
        return "hard"
    return "medium"


def simplify_scope(spec: GenreSpec, difficulty: str) -> Dict[str, Any]:
    """难度 → 本作的生成范围。

    easy  ：直接生成简易可玩成品（MVP）——只做核心循环 + 基线，实体减量
    medium：核心循环 + 基线 + 1 条拓展
    hard  ：分期生成（间接）——第一期 MVP，第二期按多条拓展方向增强，实体加量
    """
    tiers = {
        "easy": {
            "extensions": 0, "entity_scale": 0.6, "mode": "direct_mvp",
            "scope_note": "直接生成简易可玩成品（MVP）：只实现核心循环与基本功能基线，不做拓展，实体数量精简",
        },
        "medium": {
            "extensions": 1, "entity_scale": 1.0, "mode": "direct",
            "scope_note": "实现核心循环、基本功能基线与 1 条拓展方向",
        },
        "hard": {
            "extensions": 3, "entity_scale": 1.4, "mode": "staged",
            "scope_note": "分期生成：第一期 MVP（核心循环+基线），第二期按拓展方向逐项增强",
        },
    }
    tier = tiers.get(difficulty, tiers["medium"])
    return {
        "difficulty": difficulty,
        "mode": tier["mode"],
        "extensions": spec.extensions[: tier["extensions"]],
        "entity_scale": tier["entity_scale"],
        "scope_note": tier["scope_note"],
    }


def scale_count(count: int, entity_scale: float) -> int:
    """按难度缩放蓝图实体数量（最少保 1）。"""
    import math

    return max(1, math.ceil(count * entity_scale))


# ═══════════════════════════════════════════════════════════════
# LLM 提示注入：把品类规格变成 planner / code_generator 的提示词片段
# ═══════════════════════════════════════════════════════════════

def build_genre_prompt_hint(
    requirements: str,
    max_extensions: int = 3,
    difficulty: Optional[str] = None,
) -> str:
    """匹配品类并产出注入 LLM 的规格提示；无法匹配返回空串。

    difficulty 缺省时从需求文本推断；easy 直接出简易成品，hard 分期规划。
    """
    spec = match_genre(requirements)
    if spec is None:
        return ""
    difficulty = difficulty or infer_difficulty(requirements)
    scope = simplify_scope(spec, difficulty)
    lines = [
        f"[品类规格] {spec.name_zh}（基款：{spec.representative}）",
        f"[难度档位] {difficulty} → {scope['scope_note']}",
        f"核心循环：{spec.core_loop}",
        "必须实现的核心机制：",
    ]
    lines += [f"- {m}" for m in spec.mechanics]
    lines.append("基本功能基线（缺一不可）：")
    lines += [f"- {f}" for f in UNIVERSAL_BASELINE]
    if scope["extensions"]:
        lines.append("本作选定的拓展方向：")
        lines += [f"- {e}" for e in scope["extensions"]]
    if difficulty == "hard":
        lines.append("任务分期：把 MVP 与增强项拆成先后两期任务（第二期依赖第一期）")
    lines.append(f"胜利条件：{spec.win_condition}")
    lines.append(f"失败条件：{spec.lose_condition}")
    if spec.hud_extras:
        lines.append(f"HUD 额外显示：{('、'.join(spec.hud_extras))}")
    return "\n".join(lines)
