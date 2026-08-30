"""GameForge - 机械验收门禁（Baseline Checker）

对生成的 Godot 项目做**确定性**检查（不依赖 LLM 自觉），
对应 genre_specs.UNIVERSAL_BASELINE 的基本功能底线：

- 场景结构与上线件（GameFlow/开始/暂停/结束面板）
- 玩家输入控制（而非演示自动玩）
- 计分系统闭环（pickup → game_flow → HUD）
- 音效/图标/导出预设齐备
- .tscn 引用的所有 ExtResource 在磁盘上真实存在（防缺文件）

失败项结构化返回，供发布门禁与 workflow 的修复回路消费。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import structlog

logger = structlog.get_logger()


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def check_project(project_path: str) -> Dict[str, Any]:
    """对项目执行基线检查。返回 {ok, passed, failures}。"""
    tscn_path = os.path.join(project_path, "scenes", "main.tscn")
    tscn = _read(tscn_path)
    pg = _read(os.path.join(project_path, "project.godot"))
    gf = _read(os.path.join(project_path, "addons", "gameforge", "runtime", "game_flow.gd"))
    player = _read(os.path.join(project_path, "addons", "gameforge", "runtime", "player.gd"))
    pickup = _read(os.path.join(project_path, "addons", "gameforge", "runtime", "pickup.gd"))
    sfx_dir = os.path.join(project_path, "assets", "sfx")

    checks: List[Dict[str, Any]] = []

    def _check(cid: str, desc: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": cid, "desc": desc, "ok": bool(ok), "detail": detail})

    # 1. 场景存在
    _check("scene_exists", "主场景文件存在", bool(tscn), tscn_path)

    # 2. 上线件结构：GameFlow + 三面板
    has_flow = 'name="GameFlow"' in tscn
    _check("game_flow", "游戏流程层（GameFlow）", has_flow)
    for panel in ("BootPanel", "PausePanel", "OverPanel"):
        _check(f"panel_{panel}", f"{panel} 面板", f'name="{panel}"' in tscn)
    _check("restart_button", "重开按钮与信号连接", "RestartBtn" in tscn and "_on_restart_pressed" in tscn)

    # 3. 玩家输入控制（基线：不允许纯演示自动玩）
    _check(
        "player_input",
        "玩家输入控制（方向轴 + 跳跃键）",
        bool(re.search(r"Input\.get_axis\(", player)) and "ui_accept" in player,
    )
    _check("player_death", "死亡判定（坠出/触敌）", "fall_limit" in player and "begins_with(\"Enemy\")" in player)

    # 4. 计分闭环
    _check(
        "score_loop",
        "计分闭环（pickup → game_flow.add_score → HUD）",
        "add_score" in pickup and "add_score" in gf and "SCORE" in gf,
    )

    # 5. 暂停
    _check("pause", "暂停与恢复（Esc）", "toggle_pause" in gf and "KEY_ESCAPE" in gf)

    # 6. 音效资产
    sfx = [f for f in (os.listdir(sfx_dir) if os.path.isdir(sfx_dir) else []) if f.endswith(".wav")]
    _check("sfx_assets", "8-bit 音效（≥4 个 wav）", len(sfx) >= 4, ",".join(sorted(sfx)))

    # 7. 图标与导出预设
    _check("icon", "游戏图标", os.path.isfile(os.path.join(project_path, "icon.png")))
    presets = _read(os.path.join(project_path, "export_presets.cfg"))
    _check("export_presets", "导出预设（Web+Windows）", 'platform="Web"' in presets and 'platform="Windows Desktop"' in presets)

    # 8. .tscn 的 ExtResource 全部真实存在（防缺文件上线）
    missing: List[str] = []
    for m in re.finditer(r'\[ext_resource type="[^"]+" path="([^"]+)"', tscn):
        rel = m.group(1)
        if rel.startswith("res://"):
            disk = os.path.join(project_path, rel[len("res://"):].replace("/", os.sep))
            if not os.path.isfile(disk):
                missing.append(rel)
    _check("ext_resources", "场景引用的脚本/纹理全部存在", not missing, ",".join(missing[:3]))

    # 9. 主场景注册
    _check("main_scene", "project.godot 注册主场景", 'run/main_scene="res://scenes/main.tscn"' in pg)

    failures = [c for c in checks if not c["ok"]]
    result = {
        "ok": not failures,
        "passed": [c["check"] for c in checks if c["ok"]],
        "failures": [{"check": c["check"], "desc": c["desc"], "detail": c["detail"]} for c in failures],
    }
    if failures:
        logger.warning("baseline_checker.failed", failures=[f["check"] for f in failures])
    else:
        logger.info("baseline_checker.passed", checks=len(checks))
    return result
