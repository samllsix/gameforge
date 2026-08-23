# -*- coding: utf-8 -*-
"""渲染 GameForge 系统架构图（公司类比双栏形式）→ docs/gameforge_architecture_analogy.png"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

TXT = "#171717"
MUT = "#52525B"
GRAY_EDGE = "#E4E4E7"

LAYERS = {
    "user":    {"light": "#EFF6FF", "edge": "#2563EB", "dark": "#1E40AF"},
    "harness": {"light": "#F0FDF4", "edge": "#16A34A", "dark": "#166534"},
    "runtime": {"light": "#FFFBEB", "edge": "#D97706", "dark": "#92400E"},
    "agent":   {"light": "#FAF5FF", "edge": "#7C3AED", "dark": "#5B21B6"},
    "tools":   {"light": "#FFF7ED", "edge": "#EA580C", "dark": "#9A3412"},
}
TEAL = {"light": "#F0FDFA", "edge": "#0D9488", "dark": "#0F766E"}

fig = plt.figure(figsize=(13, 18.2), dpi=120)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 100)
ax.set_ylim(0, 140)
ax.axis("off")


def box(x, y, w, h, fill="#FFFFFF", edge=GRAY_EDGE, lw=1.2, rs=0.9, zorder=3):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0,rounding_size={rs}",
                                facecolor=fill, edgecolor=edge, linewidth=lw, zorder=zorder))


def arrow(x1, y1, x2, y2, color="#71717A", lw=1.6, ls="-", zorder=4, ms=13):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=ms, color=color, linewidth=lw,
                                 linestyle=ls, shrinkA=0, shrinkB=0, zorder=zorder,
                                 capstyle="round", joinstyle="round"))


# ══ 标题 ══
ax.text(50, 137, "GameForge 系统架构 · 公司组织类比", fontsize=23, fontweight="bold",
        color=TXT, ha="center", va="center")
ax.text(50, 134, "用户提需求 → Harness 管理层 → Runtime 运行时 → 6 Agent 干活 → Godot 工具出画面",
        fontsize=12, color=MUT, ha="center", va="center")
ax.text(14.25, 131, "公司组织架构类比", fontsize=13, fontweight="bold", color=MUT,
        ha="center", va="center")
ax.text(63.5, 131, "GameForge 系统架构", fontsize=13, fontweight="bold", color=TXT,
        ha="center", va="center")

LX, LW = 2.5, 23.5      # 左栏
RX, RW = 29.5, 68       # 右栏

# ══ 层 Y 范围 ══
Y = {"user": (121.5, 7.5), "harness": (100, 19), "runtime": (78.5, 19),
     "agent": (53, 23), "tools": (34, 16.5)}
CY = {k: v[0] + v[1] / 2 for k, v in Y.items()}

# ══ 左栏 类比框 ══
LEFT = [
    ("user", "老　板", ["制定目标 · 分配任务"]),
    ("harness", "公司管理层", ["制定制度", "管理资源和人"]),
    ("runtime", "部门 / 办公室", ["提供办公环境", "和基础设施"]),
    ("agent", "员　工", ["具体干活的人", "完成任务"]),
    ("tools", "员工工具", ["电脑 · 软件", "资料 · 设备"]),
]
left_boxes = {}
for key, title, descs in LEFT:
    c = LAYERS[key]
    ly, lh = Y[key]
    bh = min(lh, 13) if lh > 8 else lh
    by = ly + (lh - bh) / 2
    box(LX, by, LW, bh, fill=c["light"], edge=c["edge"], lw=1.8, rs=1.2)
    left_boxes[key] = (by, bh)
    cx = LX + LW / 2
    if bh <= 8:
        ax.text(cx, by + bh * 0.64, title, fontsize=13, fontweight="bold",
                color=c["dark"], ha="center", va="center")
        ax.text(cx, by + bh * 0.27, descs[0], fontsize=10, color=MUT,
                ha="center", va="center")
    else:
        ax.text(cx, by + bh - 3, title, fontsize=13.5, fontweight="bold",
                color=c["dark"], ha="center", va="center")
        for i, d in enumerate(descs):
            ax.text(cx, by + bh - 6.2 - i * 2.6, d, fontsize=10, color=MUT,
                    ha="center", va="center")

# 左栏层间箭头
order = ["user", "harness", "runtime", "agent", "tools"]
for a, b in zip(order, order[1:]):
    y1 = left_boxes[a][0]
    y2 = left_boxes[b][0] + left_boxes[b][1]
    arrow(14.25, y1, 14.25, y2)

# 左右映射虚线
for key in order:
    ax.plot([LX + LW + 0.4, RX - 0.4], [CY[key], CY[key]],
            color="#A1A1AA", lw=1.2, ls=(0, (3, 3)), zorder=2)


def layer_container(key, title):
    c = LAYERS[key]
    y, h = Y[key]
    box(RX, y, RW, h, fill="#FFFFFF", edge=c["edge"], lw=1.6, rs=1.3, zorder=1)
    ax.text(RX + 1.8, y + h - 2.4, title, fontsize=13, fontweight="bold",
            color=c["dark"], ha="left", va="center")
    return y, h


def module_row(key, mods, my, mh, gap=0.7):
    """mods: [(name, cap1, cap2|None, hot|None)]"""
    c = LAYERS[key]
    n = len(mods)
    mw = (RW - 3 - gap * (n - 1)) / n
    for i, m in enumerate(mods):
        name, cap1, cap2 = m[0], m[1], m[2]
        hot = m[3] if len(m) > 3 else None
        st = TEAL if hot else c
        mx = RX + 1.5 + i * (mw + gap)
        box(mx, my, mw, mh, fill=st["light"], edge=st["edge"], lw=1.1)
        ax.text(mx + mw / 2, my + mh - 2.6, name, fontsize=11.5, fontweight="bold",
                color=st["dark"], ha="center", va="center")
        ax.text(mx + mw / 2, my + mh * 0.45, cap1, fontsize=9.5, color=MUT,
                ha="center", va="center")
        if cap2:
            ax.text(mx + mw / 2, my + mh * 0.2, cap2, fontsize=9.5, color=MUT,
                    ha="center", va="center")


# ══ 右栏 L0 用户 ══
c = LAYERS["user"]
y, h = Y["user"]
box(RX, y, RW, h, fill=c["light"], edge=c["edge"], lw=1.6, rs=1.3)
ax.text(RX + 3, y + h * 0.62, "用户", fontsize=13.5, fontweight="bold",
        color=c["dark"], ha="left", va="center")
ax.text(RX + 3, y + h * 0.26, "提出需求：“做一个 2D 跳跃游戏”　→　POST /api/v1/generate",
        fontsize=10.5, color=MUT, ha="left", va="center")

# ══ 右栏 L1 Harness ══
layer_container("harness", "Agent Harness · 管理层（接入与治理）")
module_row("harness", [
    ("组织管理", "Agent 注册表", "6 角色分配"),
    ("任务管理", "task_plan 拆解", "进度跟踪"),
    ("资源与权限", "API Key 鉴权", "限流 ≤ 20 并发"),
    ("监控评估", "Prometheus 指标", "评分 · 审计"),
    ("插件扩展", "引擎适配层", "可换引擎"),
], my=101.2, mh=13.4)

# ══ 右栏 L2 Runtime ══
layer_container("runtime", "Agent Runtime · 运行时（编排与执行）")
module_row("runtime", [
    ("执行引擎", "LangGraph", "工作流状态机"),
    ("上下文管理", "project_context", "会话 · 记忆"),
    ("工具调用", "Godot 调用", "结果处理 · 重试"),
    ("状态管理", "DB 持久化", "断点续跑"),
    ("通信机制", "SSE 流式推送", "事件通知"),
], my=79.7, mh=13.4)

# ══ 右栏 L3 Agent（核心） ══
layer_container("agent", "Agent · 智能体层（6 位 AI 员工）— 核心")
c = LAYERS["agent"]
comp_w = (RW - 3 - 2 * 1.5) / 3
comps = [("角色设定", "专职角色 · 职责边界"),
         ("LLM 大脑", "推理 · 理解 · 生成"),
         ("ReAct 方法", "思考 → 行动 → 观察")]
for i, (name, cap) in enumerate(comps):
    mx = RX + 1.5 + i * (comp_w + 1.5)
    box(mx, 61, comp_w, 10, fill=c["light"], edge=c["edge"], lw=1.2)
    ax.text(mx + comp_w / 2, 67.5, name, fontsize=12, fontweight="bold",
            color=c["dark"], ha="center", va="center")
    ax.text(mx + comp_w / 2, 63.6, cap, fontsize=9.5, color=MUT,
            ha="center", va="center")
arrow(RX + 1.5 + comp_w + 0.2, 66, RX + 1.5 + comp_w + 1.3, 66, lw=1.4, ms=10)
arrow(RX + 1.5 + 2 * (comp_w + 1.5) - 1.3, 66, RX + 1.5 + 2 * (comp_w + 1.5) - 0.2, 66,
      lw=1.4, ms=10)
chips = ["需求分析", "代码生成", "资源美术", "场景设计", "测试运行", "评　审"]
chip_w = (RW - 3 - 5 * 0.6) / 6
for i, name in enumerate(chips):
    mx = RX + 1.5 + i * (chip_w + 0.6)
    box(mx, 54.5, chip_w, 5, fill="#FFFFFF", edge=c["edge"], lw=1.1)
    ax.text(mx + chip_w / 2, 57, name, fontsize=10, fontweight="bold",
            color=c["dark"], ha="center", va="center")

# ══ 右栏 L4 Tools ══
layer_container("tools", "Tools & Knowledge · 工具与知识")
tool_w = (RW - 3 - 3 * 0.7) / 4
tools = [
    [("Godot 引擎", "headless 运行"), ("场景转换", "SceneIR → tscn"),
     ("语法检查", "syntax_check.gd"), ("实时预览", "mss 截图 → PNG", True)],
    [("文件系统", "scenes · scripts"), ("数据库", "任务 · 历史 · 评测"),
     ("模板知识库", "游戏模板库"), ("REST API", "/api/v1/*")],
]
for r, row in enumerate(tools):
    ty = 41.8 - r * 6.8
    for i, t in enumerate(row):
        name, cap = t[0], t[1]
        hot = t[2] if len(t) > 2 else None
        st = TEAL if hot else LAYERS["tools"]
        mx = RX + 1.5 + i * (tool_w + 0.7)
        box(mx, ty, tool_w, 6, fill=st["light"], edge=st["edge"], lw=1.2)
        ax.text(mx + tool_w / 2, ty + 3.9, name, fontsize=11, fontweight="bold",
                color=st["dark"], ha="center", va="center")
        ax.text(mx + tool_w / 2, ty + 1.7, cap, fontsize=9, color=MUT,
                ha="center", va="center")

# ══ 底部左：术语映射表 ══
box(2.5, 2.5, 44, 28.5, fill="#FFFFFF", edge="#D4D4D8", lw=1.3, rs=1.3, zorder=1)
ax.text(4.5, 28.4, "术语映射表", fontsize=13, fontweight="bold", color=TXT,
        ha="left", va="center")
rows = [
    ("GameForge", "公司类比", "作用"),
    ("API 网关", "前台接待", "接收需求 · 返回结果"),
    ("Harness", "管理层", "管人 · 管资源 · 管安全"),
    ("Runtime", "办公环境", "执行环境与基础设施"),
    ("Agent ×6", "员工", "具体干活 · 完成任务"),
    ("ReAct", "工作方法", "生成 → 测试 → 修复"),
    ("Tools", "工具设备", "Godot · 截图 · 文件 · DB"),
    ("用户", "老板", "提出需求 · 验收结果"),
]
col_x = [4, 17, 28.5]
col_w = [13, 11.5, 19.5]
rh = 2.95
top = 26.3
for r, row in enumerate(rows):
    ry = top - (r + 1) * rh
    if r == 0:
        box(4, ry, 40, rh, fill="#F0F0F3", edge="none", lw=0, rs=0.3, zorder=2)
    elif r % 2 == 0:
        box(4, ry, 40, rh, fill="#FAFAFB", edge="none", lw=0, rs=0.3, zorder=2)
    for ci, cell in enumerate(row):
        bold = r == 0
        color = MUT if r == 0 else TXT
        ax.text(col_x[ci] + 1, ry + rh / 2, cell,
                fontsize=9.5, fontweight="bold" if bold else "normal",
                color=color, ha="left", va="center", zorder=3)
ax.plot([4, 44], [top - rh, top - rh], color="#D4D4D8", lw=1, zorder=3)
for r in range(1, len(rows)):
    yline = top - r * rh
    ax.plot([4, 44], [yline, yline], color="#ECECEE", lw=0.7, zorder=3)
for cx in (col_x[1], col_x[2]):
    ax.plot([cx, cx], [top - len(rows) * rh + 0.4, top], color="#ECECEE", lw=0.7, zorder=3)

# ══ 底部右：工作流程 ══
box(50, 2.5, 47.5, 28.5, fill="#FFFFFF", edge="#D4D4D8", lw=1.3, rs=1.3, zorder=1)
ax.text(52, 28.4, "工作流程 · 一次游戏生成", fontsize=13, fontweight="bold",
        color=TXT, ha="left", va="center")
steps = [
    ("1", "用户提需求", "POST /api/v1/generate", False),
    ("2", "理解目标", "需求分析师 → GameDesignModel", False),
    ("3", "ReAct 循环", "生成 → 编译 → 测试 → 修复", True),
    ("4", "调用工具", "Godot headless 运行 + mss 截图", False),
    ("5", "交付", "游戏 + 评测报告 + 实时预览", False),
]
sh, sgap = 3.7, 1.35
stop = 26.3
step_pos = []
for i, (num, title, cap, hot) in enumerate(steps):
    sy = stop - (i + 1) * sh - i * sgap
    step_pos.append((sy, sy + sh))
    st = LAYERS["agent"] if hot else {"light": "#FFFFFF", "edge": GRAY_EDGE, "dark": TXT}
    box(51.5, sy, 44.5, sh, fill=st["light"], edge=st["edge"], lw=1.4 if hot else 1.1)
    ax.add_patch(plt.Circle((53.8, sy + sh / 2), 1.15,
                            facecolor=st["edge"] if hot else "#A1A1AA",
                            edgecolor="none", zorder=4))
    ax.text(53.8, sy + sh / 2, num, fontsize=10, fontweight="bold", color="white",
            ha="center", va="center", zorder=5)
    ax.text(55.8, sy + sh / 2, title, fontsize=11, fontweight="bold",
            color=st["dark"], ha="left", va="center", zorder=4)
    ax.text(94.8, sy + sh / 2, cap, fontsize=9.5, color=MUT,
            ha="right", va="center", zorder=4)
for i in range(len(steps) - 1):
    y1 = step_pos[i][0]
    y2 = step_pos[i + 1][1]
    arrow(73.5, y1, 73.5, y2, lw=1.4, ms=10)
# ReAct 自环（虚线）
s3b, s3t = step_pos[2]
arrow(96, s3b + 0.6, 96, s3t - 0.6, color=LAYERS["agent"]["edge"], ls=(0, (3, 3)),
      lw=1.5, ms=11)
ax.text(96.8, (s3b + s3t) / 2, "循环", fontsize=9, color=LAYERS["agent"]["edge"],
        ha="center", va="center", zorder=4, rotation=90)

out = r"D:\game_project\docs\gameforge_architecture_analogy.png"
fig.savefig(out, dpi=120, facecolor="white")
print("saved:", out)
