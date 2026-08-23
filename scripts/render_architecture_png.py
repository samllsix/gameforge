# -*- coding: utf-8 -*-
"""渲染 GameForge 功能架构图 → docs/gameforge_architecture.png"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BRAND = "#4B3FE3"
BRAND_FILL = "#EDEDFD"
BRAND_TEXT = "#1A1759"
ACCENT = "#0D9488"
ACCENT_FILL = "#E6F7F4"
ACCENT_TEXT = "#0F766E"
BOX_FILL = "#F0F0F3"
BOX_EDGE = "#D4D4D8"
CNT_FILL = "#FAFAFB"
CNT_EDGE = "#D9D9DE"
TXT = "#171717"
MUT = "#52525B"
ARR = "#71717A"

fig = plt.figure(figsize=(14, 15.1), dpi=120)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 100)
ax.set_ylim(0, 108)
ax.axis("off")
ax.set_facecolor("white")


def box(x, y, w, h, fill=BOX_FILL, edge=BOX_EDGE, lw=1.2, rs=1.0, zorder=3):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rs}",
                       facecolor=fill, edgecolor=edge, linewidth=lw, zorder=zorder)
    ax.add_patch(p)
    return p


def arrow(x1, y1, x2, y2, color=ARR, lw=1.7, ls="-", scale=15, zorder=4):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=scale,
                        color=color, linewidth=lw, linestyle=ls,
                        shrinkA=0, shrinkB=0, zorder=zorder,
                        capstyle="round", joinstyle="round")
    ax.add_patch(a)


# ── 标题 ──
ax.text(37, 105.5, "GameForge 功能架构", fontsize=24, fontweight="bold",
        color=TXT, ha="center", va="center")
ax.text(37, 102.6, "五层调用链 · 智能体流水线为核心 · 右侧横切能力贯穿各层",
        fontsize=12, color=MUT, ha="center", va="center")

# ══ L1 接入层 ══
box(5, 85, 64, 16.5, fill=CNT_FILL, edge=CNT_EDGE, lw=1.3, rs=1.6, zorder=1)
ax.text(7.5, 97.5, "接入层 · FastAPI :8000", fontsize=14, fontweight="bold",
        color=TXT, ha="left", va="center")
l1 = [("任务 API", "generate / SSE"), ("查询 API", "tasks / history"),
      ("预览 API", "frame / stats"), ("运维 API", "health / metrics")]
for i, (name, cap) in enumerate(l1):
    bx = 7.5 + i * 15.17
    box(bx, 87, 13.5, 10)
    cx = bx + 6.75
    ax.text(cx, 94.2, name, fontsize=13, fontweight="bold", color=TXT, ha="center", va="center")
    ax.text(cx, 90.2, cap, fontsize=10, color=MUT, ha="center", va="center")

arrow(37, 85, 37, 81.6)
ax.text(39, 83.3, "下发任务", fontsize=10.5, color=MUT, ha="left", va="center")

# ══ L2 编排层 ══
box(5, 73, 64, 8.5, fill=CNT_FILL, edge=CNT_EDGE, lw=1.3, rs=1.6, zorder=1)
ax.text(7.5, 78, "编排层 · LangGraph 工作流", fontsize=14, fontweight="bold",
        color=TXT, ha="left", va="center")
ax.text(7.5, 74.8, "task_plan 拆解 → 按序调度 → 状态持久化 / 断点续跑",
        fontsize=10.5, color=MUT, ha="left", va="center")

arrow(37, 73, 37, 69.6)
ax.text(39, 71.3, "调度执行", fontsize=10.5, color=MUT, ha="left", va="center")

# ══ L3 智能体层（核心，紫框） ══
box(5, 40.5, 64, 29, fill="#FBFBFD", edge=BRAND, lw=2.0, rs=1.6, zorder=1)
ax.text(7.5, 66, "智能体层 · 6 Agent 流水线", fontsize=14, fontweight="bold",
        color=BRAND_TEXT, ha="left", va="center")

agents_r1 = [("需求分析", "→ 设计文档"), ("代码生成", "→ *.gd 脚本"), ("资源美术", "→ 贴图精灵")]
agents_r2 = [("评 审", "→ 评分报告"), ("测试运行", "→ 测试报告"), ("场景设计", "→ main.tscn")]
for i, (name, cap) in enumerate(agents_r1):
    bx = 7.5 + i * 21.75
    box(bx, 55, 17.5, 10, fill=BRAND_FILL, edge=BRAND, lw=1.3)
    cx = bx + 8.75
    ax.text(cx, 62.2, name, fontsize=13, fontweight="bold", color=BRAND_TEXT, ha="center", va="center")
    ax.text(cx, 58.2, cap, fontsize=10, color=BRAND, ha="center", va="center")
for i, (name, cap) in enumerate(agents_r2):
    bx = 7.5 + i * 21.75
    box(bx, 42, 17.5, 10, fill=BRAND_FILL, edge=BRAND, lw=1.3)
    cx = bx + 8.75
    ax.text(cx, 49.2, name, fontsize=13, fontweight="bold", color=BRAND_TEXT, ha="center", va="center")
    ax.text(cx, 45.2, cap, fontsize=10, color=BRAND, ha="center", va="center")

# 蛇形流水线箭头
arrow(25.2, 60, 28.0, 60)
arrow(45.9, 60, 48.7, 60)
arrow(57.75, 55, 57.75, 52.4)          # 资源美术 ↓ 场景设计
arrow(48.8, 47, 46.0, 47)              # 场景设计 → 测试运行
arrow(28.0, 47, 25.2, 47)              # 测试运行 → 评审
# ReAct 修复循环（虚线紫，向上）
arrow(37, 52, 37, 54.6, color=BRAND, ls=(0, (4, 3)), lw=1.6)
ax.text(38.8, 53.3, "未通过 · 修复", fontsize=9.5, color=BRAND, ha="left", va="center")

arrow(37, 40.5, 37, 37.1)
ax.text(39, 38.8, "调用工具", fontsize=10.5, color=MUT, ha="left", va="center")

# ══ L4 引擎层 ══
box(5, 21.5, 64, 15.5, fill=CNT_FILL, edge=CNT_EDGE, lw=1.3, rs=1.6, zorder=1)
ax.text(7.5, 33.5, "引擎层 · Godot 工具链", fontsize=14, fontweight="bold",
        color=TXT, ha="left", va="center")
l4 = [("Godot 引擎", "headless 运行", False), ("场景转换", "SceneIR→tscn", False),
      ("进程管理", "supervisor", False), ("画面截取", "mss → PNG", True)]
for i, (name, cap, hot) in enumerate(l4):
    bx = 7.5 + i * 15.17
    if hot:
        box(bx, 23, 13.5, 10, fill=ACCENT_FILL, edge=ACCENT, lw=1.6)
        c_name, c_cap = ACCENT_TEXT, ACCENT_TEXT
    else:
        box(bx, 23, 13.5, 10)
        c_name, c_cap = TXT, MUT
    cx = bx + 6.75
    ax.text(cx, 30.2, name, fontsize=13, fontweight="bold", color=c_name, ha="center", va="center")
    ax.text(cx, 26.2, cap, fontsize=10, color=c_cap, ha="center", va="center")

arrow(37, 21.5, 37, 18.1)
ax.text(39, 19.8, "读写", fontsize=10.5, color=MUT, ha="left", va="center")

# ══ L5 数据层 ══
box(5, 10, 64, 8, fill=CNT_FILL, edge=CNT_EDGE, lw=1.3, rs=1.6, zorder=1)
ax.text(7.5, 15.2, "数据层", fontsize=14, fontweight="bold", color=TXT, ha="left", va="center")
box(7.5, 11, 28, 4.6)
ax.text(21.5, 13.3, "SQLAlchemy · 任务 / 历史 / 评测", fontsize=10.5, color=MUT,
        ha="center", va="center")
box(38, 11, 28, 4.6)
ax.text(52, 13.3, "文件系统 · scenes / scripts / assets", fontsize=10.5, color=MUT,
        ha="center", va="center")

# ══ 右栏 横切能力 ══
box(72, 10, 23, 91.5, fill=CNT_FILL, edge=CNT_EDGE, lw=1.3, rs=1.6, zorder=1)
ax.text(83.5, 97.5, "横切能力", fontsize=14, fontweight="bold", color=TXT, ha="center", va="center")
ax.text(83.5, 94.8, "贯穿各层", fontsize=10.5, color=MUT, ha="center", va="center")

box(74, 76, 19, 15)
ax.text(83.5, 87.5, "安全防护", fontsize=12.5, fontweight="bold", color=TXT, ha="center", va="center")
for j, line in enumerate(["API Key 鉴权", "限流 ≤ 20 并发", "路径防穿越"]):
    ax.text(83.5, 83.6 - j * 3, line, fontsize=10.5, color=MUT, ha="center", va="center")

box(74, 61, 19, 12)
ax.text(83.5, 69.8, "可观测", fontsize=12.5, fontweight="bold", color=TXT, ha="center", va="center")
ax.text(83.5, 66, "Prometheus 指标", fontsize=10.5, color=MUT, ha="center", va="center")
ax.text(83.5, 63, "结构化日志", fontsize=10.5, color=MUT, ha="center", va="center")

box(74, 26, 19, 31, fill="#F7FCFB", edge=ACCENT, lw=1.6)
ax.text(83.5, 53.5, "实时预览链路", fontsize=12.5, fontweight="bold",
        color=ACCENT_TEXT, ha="center", va="center")
box(76, 46, 15, 5)
ax.text(83.5, 48.5, "Godot 渲染", fontsize=10.5, color=TXT, ha="center", va="center")
arrow(83.5, 46, 83.5, 43.7)
box(76, 38, 15, 5)
ax.text(83.5, 40.5, "mss 截图", fontsize=10.5, color=TXT, ha="center", va="center")
arrow(83.5, 38, 83.5, 35.7)
box(76, 30, 15, 5)
ax.text(83.5, 32.5, "PNG → 前端", fontsize=10.5, color=TXT, ha="center", va="center")
ax.text(83.5, 27.6, "真渲染画面 · /preview/frame", fontsize=9, color=ACCENT_TEXT,
        ha="center", va="center")

# ══ 图例 ══
box(7.5, 3.4, 2.2, 2.2, fill=BRAND_FILL, edge=BRAND, lw=1.3, rs=0.4)
ax.text(10.6, 4.5, "核心：智能体流水线", fontsize=10.5, color=MUT, ha="left", va="center")
box(33, 3.4, 2.2, 2.2, fill=ACCENT_FILL, edge=ACCENT, lw=1.3, rs=0.4)
ax.text(36.1, 4.5, "特色：实时预览", fontsize=10.5, color=MUT, ha="left", va="center")
ax.plot([53, 57], [4.5, 4.5], color=BRAND, lw=1.6, ls=(0, (4, 3)))
ax.text(58, 4.5, "ReAct 修复循环", fontsize=10.5, color=MUT, ha="left", va="center")

out = r"D:\game_project\docs\gameforge_architecture.png"
fig.savefig(out, dpi=120, facecolor="white", bbox_inches=None)
print("saved:", out)
