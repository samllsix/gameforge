"""GameForge - Godot playtest 运行器（输入回放 + 帧捕获证据链）。

借鉴 GameFactory-3A 的 validate-play-iterate 闭环："编译通过/能启动"
不算验证，必须真正驱动玩家操作、逐帧留证、按报告评分：

    生成动作脚本 → 注入录制器 autoload → --headless 启动游戏 →
    进程内注入输入事件 + 抓帧 → report.json → evaluate_report() 评分

产物全部经 src/core/paths 编址::

    <project>/.gameforge/<run_id>/playtest/
        actions.json   声明式输入脚本（可重放）
        frames/f*.png  固定步长截图
        report.json    执行报告

用法::

    runner = PlaytestRunner(config)
    result = runner.run(actions, project_id="gf_xxx", run_id="20260906_120000")
    if not result["ok"]:
        ...  # 把 result["findings"] 喂给 debugger 修复
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from src.core import paths

logger = structlog.get_logger()

REPORT_SCHEMA = "gameforge.playtest_report.v1"

#: 内置 ui_* 动作在所有 Godot 项目里默认存在（方向键/空格/回车），
#: 不依赖生成侧 input_map 配置，作为通用冒烟操作最稳。
_DEFAULT_PLAN: List[Dict[str, Any]] = [
    {"t": 0.5, "type": "key", "key": "Right", "pressed": True},
    {"t": 2.0, "type": "key", "key": "Right", "pressed": False},
    {"t": 2.3, "type": "key", "key": "Space", "pressed": True},
    {"t": 2.5, "type": "key", "key": "Space", "pressed": False},
    {"t": 2.8, "type": "key", "key": "Left", "pressed": True},
    {"t": 4.3, "type": "key", "key": "Left", "pressed": False},
    {"t": 4.6, "type": "key", "key": "Space", "pressed": True},
    {"t": 4.8, "type": "key", "key": "Space", "pressed": False},
    {"t": 5.1, "type": "key", "key": "Up", "pressed": True},
    {"t": 5.6, "type": "key", "key": "Up", "pressed": False},
]

RECORDER_SOURCE = Path(__file__).resolve().parents[3] / "addons" / "gameforge" / "playtest_recorder.gd"
RECORDER_AUTOLOAD_NAME = "GameForgePlaytestRecorder"


def build_default_action_plan() -> List[Dict[str, Any]]:
    """通用冒烟动作脚本：左右移动 + 两次跳跃 + 一次交互键。

    内置 ui_* 动作（ui_right/ui_left/ui_accept/ui_up）由方向键/空格/回车
    触发，所有 Godot 项目自带，不依赖生成侧的 input_map。
    """
    return [dict(a) for a in _DEFAULT_PLAN]


def evaluate_report(
    report: Optional[Dict[str, Any]],
    *,
    max_console_errors: int = 0,
    console_errors: Optional[List[Dict[str, Any]]] = None,
    require_frames: bool = False,
) -> Dict[str, Any]:
    """按 GF-3A playtest.eval 的思路给执行报告评分。

    只评已有产物，不重新运行游戏。检查项：
    - 报告存在且 schema 正确、ok=true
    - 动作确实被执行（actions_executed > 0）
    - 有帧证据（frames_captured > 0；require_frames=True 时为硬性要求）
    - 控制台错误数 <= max_console_errors
    """
    checks: Dict[str, Any] = {}
    errors: List[str] = []

    if not isinstance(report, dict):
        errors.append("report_missing_or_invalid")
        checks["report"] = False
    else:
        checks["report"] = True
        checks["schema"] = report.get("schema") == REPORT_SCHEMA
        checks["recorder_ok"] = report.get("ok") is True
        if report.get("fail_reason"):
            errors.append(f"recorder: {report['fail_reason']}")
        executed = int(report.get("actions_executed") or 0)
        checks["actions_executed"] = executed > 0
        if executed <= 0:
            errors.append("no_actions_executed")
        frames = int(report.get("frames_captured") or 0)
        checks["frames_captured"] = frames > 0
        if require_frames and frames <= 0:
            errors.append("no_frame_evidence")

    console_errors = console_errors or []
    checks["console_errors"] = len(console_errors) <= max_console_errors
    if not checks["console_errors"]:
        errors.append(f"console_errors={len(console_errors)}")

    required = ("report", "schema", "recorder_ok", "actions_executed", "console_errors")
    ok = bool(report) and all(checks[k] for k in required)
    if require_frames and not checks.get("frames_captured"):
        ok = False
    return {"ok": ok, "checks": checks, "errors": errors}


class PlaytestRunner:
    """驱动一次 playtest：注入录制器 → headless 跑游戏 → 收报告评分。"""

    def __init__(self, config: Dict[str, Any]):
        from src.engine.godot import _normalize_godot_path, _resolve_env

        godot_cfg = (config or {}).get("godot", {}) or {}
        self.editor_path: str = _normalize_godot_path(_resolve_env(
            godot_cfg.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        ))
        self.project_path: str = _normalize_godot_path(_resolve_env(
            godot_cfg.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        ))
        pt_cfg = (config or {}).get("playtest", {}) or {}
        self.frames_every: int = int(pt_cfg.get("frames_every", 10))
        self.duration_tail: float = float(pt_cfg.get("duration_tail", 1.0))
        self.timeout: int = int(pt_cfg.get("timeout_seconds", 90))
        self.skip_when_unavailable: bool = bool(pt_cfg.get("skip_when_unavailable", True))

    def available(self) -> tuple:
        if not self.editor_path or not os.path.isfile(self.editor_path):
            return False, "Godot 可执行文件不可用"
        if not self.project_path or not os.path.isdir(self.project_path):
            return False, "项目目录不存在"
        if not os.path.isfile(os.path.join(self.project_path, "project.godot")):
            return False, "项目缺少 project.godot"
        return True, "OK"

    def run(
        self,
        actions: List[Dict[str, Any]],
        *,
        project_id: str,
        run_id: str = paths.DEFAULT_RUN_ID,
        scene_path: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """执行一次 playtest，返回含评分的完整结果字典。"""
        out_dir = paths.playtest_dir(project_id, run_id)
        result: Dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "project_id": project_id,
            "run_id": run_id,
            "out_dir": str(out_dir),
            "skipped": False,
            "ok": False,
            "report": None,
            "console_errors": [],
            "findings": [],
        }

        ok_avail, why = self.available()
        if not ok_avail:
            if self.skip_when_unavailable:
                logger.warning("playtest.skipped", reason=why)
                result["skipped"] = True
                result["skip_reason"] = why
                return result
            result["findings"].append(f"环境不可用: {why}")
            return result

        # 1) 落盘动作脚本（可重放的输入证据）
        actions_path = paths.playtest_actions_path(project_id, run_id)
        paths.write_json(actions_path, actions)

        # 2) 注入录制器（脚本拷贝 + autoload 注册，均幂等）
        inject_error = self._inject_recorder(self.project_path)
        if inject_error:
            result["findings"].append(f"录制器注入失败: {inject_error}")
            return result

        # 3) headless 启动
        env = os.environ.copy()
        env["GAMEFORGE_PLAYTEST_ACTIONS"] = str(actions_path.resolve())
        env["GAMEFORGE_PLAYTEST_OUT"] = str(out_dir)
        env["GAMEFORGE_PLAYTEST_FRAME_INTERVAL"] = str(self.frames_every)
        cmd = [self.editor_path, "--headless", "--path", self.project_path]
        if scene_path:
            cmd.append(scene_path)

        timeout = timeout or self.timeout
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace", cwd=self.project_path, env=env,
            )
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as e:
            stderr = (e.stderr or b"")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            result["findings"].append(f"playtest 超时 ({timeout}s)，进程已终止")
        except Exception as e:  # noqa: BLE001
            result["findings"].append(f"playtest 启动失败: {e}")
            return result
        elapsed = time.monotonic() - t0
        result["elapsed_seconds"] = round(elapsed, 2)

        # 4) 收证据
        from src.engine.godot.runtime_smoke import _parse_runtime_errors

        result["console_errors"] = _parse_runtime_errors(stderr or "")
        report = paths.read_json(paths.playtest_report_path(project_id, run_id))
        if report is None:
            result["findings"].append("录制器未产出 report.json（游戏可能启动即崩溃）")
        result["report"] = report

        verdict = evaluate_report(
            report, console_errors=result["console_errors"], require_frames=False,
        )
        result["ok"] = verdict["ok"]
        result["checks"] = verdict["checks"]
        result["findings"].extend(verdict["errors"])

        # 帧产物清单（供视觉审查直接取用）
        frames_dir = paths.playtest_frames_dir(project_id, run_id)
        if frames_dir.is_dir():
            result["frames"] = sorted(p.name for p in frames_dir.glob("f*.png"))

        logger.info(
            "playtest.finished",
            project_id=project_id, ok=result["ok"],
            actions=verdict["checks"].get("actions_executed"),
            frames=verdict["checks"].get("frames_captured"),
        )
        return result

    # ── 注入 ────────────────────────────────────────────────────────────

    def _inject_recorder(self, project_path: str) -> str:
        """拷贝录制器脚本到项目并注册 autoload（均幂等）。返回错误信息或空串。"""
        try:
            target_dir = os.path.join(project_path, "addons", "gameforge")
            os.makedirs(target_dir, exist_ok=True)
            target_script = os.path.join(target_dir, "playtest_recorder.gd")
            if not os.path.isfile(target_script):
                shutil.copyfile(RECORDER_SOURCE, target_script)

            pg_path = os.path.join(project_path, "project.godot")
            if not os.path.isfile(pg_path):
                return "project.godot 不存在"
            with open(pg_path, "r", encoding="utf-8") as f:
                text = f.read()
            if RECORDER_AUTOLOAD_NAME in text:
                return ""  # 已注入

            import re

            injection = f'{RECORDER_AUTOLOAD_NAME}="*res://addons/gameforge/playtest_recorder.gd"\n'
            m = re.search(r"^\[autoload\]\s*$", text, re.MULTILINE)
            if m:
                pos = m.end()
                new_text = text[:pos] + "\n" + injection + text[pos:]
            else:
                new_text = text.rstrip() + "\n\n[autoload]\n" + injection
            with open(pg_path, "w", encoding="utf-8") as f:
                f.write(new_text)
            logger.info("playtest.recorder_injected", project=project_path)
            return ""
        except Exception as e:  # noqa: BLE001
            return str(e)
