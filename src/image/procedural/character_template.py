"""GameForge - 角色模板（程序化精灵的骨架）

每个模板定义：
- 体素网格：哪些像素该是头/躯干/手臂/腿/武器
- 调色板映射：哪个身体部位用哪个颜色
- 动画关键帧：idle / walk / attack 的姿态偏移

角色以低分辨率网格（典型 16x24）定义，再由 sprite generator 放大到目标尺寸。
"""
from typing import Dict, List, Tuple
from dataclasses import dataclass, field


# 体素网格中的"部件代号"
# . = 透明
# 0 = 头部
# 1 = 躯干
# 2 = 左手
# 3 = 右手
# 4 = 左脚
# 5 = 右脚
# 6 = 武器
# 7 = 眼睛
PART_HEAD = "0"
PART_BODY = "1"
PART_LHAND = "2"
PART_RHAND = "3"
PART_LFOOT = "4"
PART_RFOOT = "5"
PART_WEAPON = "6"
PART_EYE = "7"
PART_EMPTY = "."


@dataclass
class PoseGrid:
    """一帧姿态的 2D 网格（用字符串列表表示）"""
    cells: List[str]  # 每行字符串
    width: int = 0
    height: int = 0


@dataclass
class CharacterTemplate:
    """一个角色模板"""
    name: str
    poses: Dict[str, PoseGrid]  # pose_name -> grid
    palette_map: Dict[str, str]  # part_code -> hex color


def _grid_from_strings(lines: List[str]) -> PoseGrid:
    """把字符串列表转 PoseGrid（要求等宽）"""
    h = len(lines)
    w = max(len(l) for l in lines) if lines else 0
    cells = [l.ljust(w, ".") for l in lines]
    return PoseGrid(cells=cells, width=w, height=h)


# ─────────────────────────────────────────────────────────────────────
#  模板：humanoid（人形）—— 8 帧站立 + 8 帧步行 + 6 帧攻击
# ─────────────────────────────────────────────────────────────────────

def humanoid_template(skin: str = "#f4c8a8", cloth: str = "#3b8ce8",
                       hair: str = "#5a2a14", pants: str = "#23456e",
                       eye: str = "#222244") -> CharacterTemplate:
    """生成一个可定制颜色的人形角色"""
    # 标准人形：12 列 × 16 行
    # 头部 3x3、躯干 4x5、四肢 1x4

    # ── idle 8 帧：轻微呼吸（躯干缩 1px）──
    idle_frames = []
    for i in range(8):
        bob = (i % 2)  # 0 或 1
        lines = [
            "............",
            "..00000000..",
            "..07777770..",
            "..0" + "0" * 8 + "0..",
            "..00000000..",
            "..." + "1" * 6 + "...",
            "..." + "1" * 6 + "...",
            "..." + "1" * 6 + "...",
            "..2.." + "1" * 6 + "..3.",  # 手臂
            "..2.." + "1" * 6 + "..3.",
            "..2.." + "1" * 6 + "..3.",
            "....4.11.5....",  # 大腿
            "....4.11.5....",
            "....4.11.5....",
            "....4...5....",
            "....4...5....",
        ]
        idle_frames.append(_grid_from_strings(lines))

    # ── walk 8 帧：腿部前后摆动 ──
    walk_frames = []
    for i in range(8):
        # 4 帧一组：A 步、B 步、A 步中立、B 步中立
        phase = i % 4
        leg_pattern = {
            0: "....4..5....",  # 左前右后
            1: "....4..5....",
            2: "....4..5....",  # 中立
            3: "....5..4....",  # 右前左后
        }[phase]
        lines = [
            "............",
            "..00000000..",
            "..07777770..",
            "..0" + "0" * 8 + "0..",
            "..00000000..",
            "..." + "1" * 6 + "...",
            "..." + "1" * 6 + "...",
            "..." + "1" * 6 + "...",
            "..2.." + "1" * 6 + "..3.",
            "..2.." + "1" * 6 + "..3.",
            "..2.." + "1" * 6 + "..3.",
            leg_pattern,
            leg_pattern,
            "....4...5...." if phase < 2 else "....5...4....",
            "....4...5...." if phase < 2 else "....5...4....",
            "....4...5...." if phase < 2 else "....5...4....",
        ]
        walk_frames.append(_grid_from_strings(lines))

    # ── attack 6 帧：武器挥动 ──
    attack_frames = []
    for i in range(6):
        # 1=举剑, 2=挥, 3=收回
        if i < 2:
            arm_l, arm_r = ".2.", "..6"  # 右手举剑
        elif i == 2:
            arm_l, arm_r = ".2.", ".6."  # 挥
        else:
            arm_l, arm_r = ".2.", "..3"  # 收
        lines = [
            "............",
            "..00000000..",
            "..07777770..",
            "..0" + "0" * 8 + "0..",
            "..00000000..",
            "..." + "1" * 6 + "...",
            "..." + "1" * 6 + "...",
            "..." + "1" * 6 + "...",
            arm_l + "." + "1" * 6 + arm_r,
            arm_l + "." + "1" * 6 + arm_r,
            arm_l + "." + "1" * 6 + arm_r,
            "....4.11.5....",
            "....4.11.5....",
            "....4.11.5....",
            "....4...5....",
            "....4...5....",
        ]
        attack_frames.append(_grid_from_strings(lines))

    poses = {
        "idle": PoseGrid(
            cells=idle_frames[0].cells,  # 只取第一帧作代表
            width=idle_frames[0].width,
            height=idle_frames[0].height,
        ),
        "walk": PoseGrid(
            cells=walk_frames[0].cells,
            width=walk_frames[0].width,
            height=walk_frames[0].height,
        ),
        "attack": PoseGrid(
            cells=attack_frames[0].cells,
            width=attack_frames[0].width,
            height=attack_frames[0].height,
        ),
    }

    return CharacterTemplate(
        name="humanoid",
        poses=poses,
        palette_map={
            PART_HEAD: hair,
            PART_BODY: cloth,
            PART_LHAND: skin,
            PART_RHAND: skin,
            PART_LFOOT: pants,
            PART_RFOOT: pants,
            PART_WEAPON: "#cccccc",
            PART_EYE: eye,
            PART_EMPTY: "transparent",
        },
    )


# ─────────────────────────────────────────────────────────────────────
#  模板：slime（史莱姆）—— 4 帧呼吸
# ─────────────────────────────────────────────────────────────────────

def slime_template(body: str = "#88dd66", eye: str = "#222244") -> CharacterTemplate:
    frames = []
    for i in range(4):
        # 1=收，0=涨
        width_extend = (i % 2)  # 0 或 1
        if width_extend == 0:
            lines = [
                "............",
                "............",
                "....0000....",
                "...0000000..",
                "..000770000.",
                "..000000000.",
                "..000000000.",
                "..000000000.",
                "...0000000..",
                "....0000....",
                "............",
                "............",
            ]
        else:
            lines = [
                "............",
                "...00000....",
                "..00000000..",
                ".0000770000.",
                ".0000000000.",
                ".0000000000.",
                ".0000000000.",
                ".0000000000.",
                ".0000000000.",
                "..00000000..",
                "...00000....",
                "............",
            ]
        frames.append(_grid_from_strings(lines))

    poses = {
        "idle": PoseGrid(cells=frames[0].cells, width=frames[0].width, height=frames[0].height),
    }

    return CharacterTemplate(
        name="slime",
        poses=poses,
        palette_map={
            PART_HEAD: body,
            PART_BODY: body,
            PART_EYE: eye,
            PART_EMPTY: "transparent",
        },
    )


def get_template(name: str, **kwargs) -> CharacterTemplate:
    """根据名称分发模板"""
    if name == "humanoid":
        return humanoid_template(**kwargs)
    elif name == "slime":
        return slime_template(**kwargs)
    else:
        raise ValueError(f"Unknown template: {name}")