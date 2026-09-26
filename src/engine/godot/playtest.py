"""GameForge - Godot playtest 运行器（输入回放 + 帧捕获证据链）。

playtest 闭环："编译通过/能启动"
不算验证，必须真正驱动玩家操作、逐帧留证、按报告评分：

    生成动作脚本 → 注入录制器 autoload → 启动游戏 →
    进程内注入输入事件 + 抓帧 → report.json → evaluate_report() 评分

产物全部经 src/core/paths 编址，返回值遵循 src/core/result 契约::

    <project>/.gameforge/<run_id>/playtest/
        actions.json   声明式输入脚本（可重放）
        frames/f*.png  固定步长截图
        report.json    playtest 执行报告

渲染模式（playtest.rendering_mode）：
- ``headless``：`--headless` 启动。注意 Godot 4 的 headless 用 dummy 渲染器
  （没有渲染服务器），部分环境下 `get_viewport().get_texture().get_image()`
  只会得到纯色空帧——画面证据不可用（GameFactory-3A 对此行为有明确记载）。
- ``windowed``：带窗口启动，进程内抓帧拿到的是真实渲染画面；无显示器的
  机器上会启动失败。
- ``auto``（默认）：先 headless；若游戏跑通但帧证据被 detect_blank_frames
  判为空帧，自动回退窗口模式重试一次。

用法::

    runner = PlaytestRunner(config)
    result = runner.run(actions, project_id="gf_xxx", run_id="20260906_120000")
    if not result["ok"]:
        ...  # 把 result["errors"] 喂给 debugger 修复
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

from src.core import paths
from src.core.result import add_artifact, new_operation_result

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

#: 空帧判定阈值：灰度图 stddev 低于它视为纯色/空帧（游戏画面通常 > 10）。
BLANK_FRAME_STD_THRESHOLD = 2.0


def build_default_action_plan() -> List[Dict[str, Any]]:
    """通用冒烟动作脚本：左右移动 + 两次跳跃 + 一次交互键。

    内置 ui_* 动作（ui_right/ui_left/ui_accept/ui_up）由方向键/空格/回车
    触发，所有 Godot 项目自带，不依赖生成侧的 input_map。
    """
    return [dict(a) for a in _DEFAULT_PLAN]


def detect_blank_frames(
    frames_dir: Path,
    frame_names: List[str],
    *,
    std_threshold: float = BLANK_FRAME_STD_THRESHOLD,
    max_samples: int = 6,
) -> Dict[str, Any]:
    """检测帧证据是否"全空"（纯色，无实际画面）。

    用灰度图 stddev 判断：纯色帧 stddev≈0，真实游戏画面通常远高于阈值。
    返回 ``{"blank": bool|None, "checked": n, "avg_std": float|None}``；
    pillow 缺失或无帧时 ``blank=None``（无法判定，调用方不据此回退）。
    """
    try:
        from PIL import Image, ImageStat  # noqa: PLC0415
    except ImportError:
        return {"blank": None, "checked": 0, "avg_std": None}
    names = [n for n in frame_names if (frames_dir / n).is_file()]
    if not names:
        return {"blank": None, "checked": 0, "avg_std": None}
    step = max(1, len(names) // max_samples)
    samples = names[::step][:max_samples]
    stds: List[float] = []
    for name in samples:
        with Image.open(frames_dir / name) as img:
            stds.append(ImageStat.Stat(img.convert("L")).stddev[0])
    avg = sum(stds) / len(stds)
    return {"blank": bool(avg < std_threshold), "checked": len(samples), "avg_std": round(avg, 2)}


def evaluate_report(
    report: Optional[Dict[str, Any]],
    *,
    max_console_errors: int = 0,
    console_errors: Optional[List[Dict[str, Any]]] = None,
    require_frames: bool = False,
    layout_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对 playtest 执行报告逐项评分。

    只评已有产物，不重新运行游戏。检查项：
    - 报告存在且 schema 正确、ok=true
    - 动作确实被执行（actions_executed > 0）
    - 有帧证据（frames_captured > 0；require_frames=True 时为硬性要求）
    - 控制台错误数 <= max_console_errors
    - 可选手感项：report 含 player_displacement / jumps 时做最低阈值
    - 可选布局门禁：layout_report.validation.ok
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

        # 手感量化（若录制器提供了位移/跳跃统计）
        disp = report.get("player_displacement")
        if isinstance(disp, (int, float)):
            # 跑了 5s+ 的冒烟：水平位移应至少约一个身位
            checks["player_moved"] = abs(float(disp)) >= 24.0
            if not checks["player_moved"]:
                errors.append(f"player_stuck_displacement={disp}")
        jumps = report.get("jumps_executed")
        if isinstance(jumps, (int, float)):
            checks["jump_attempted"] = int(jumps) > 0
            # 非硬失败：平台关卡若完全没跳成功，只记 warning 不否决
            if int(jumps) <= 0 and report.get("genre") in (None, "platformer", "runner", "metroidvania"):
                errors.append("no_jump_detected_soft")

    console_errors = console_errors or []
    checks["console_errors"] = len(console_errors) <= max_console_errors
    if not checks["console_errors"]:
        errors.append(f"console_errors={len(console_errors)}")

    if isinstance(layout_report, dict):
        validation = layout_report.get("validation") or {}
        checks["layout_reachable"] = bool(validation.get("ok", True))
        if not checks["layout_reachable"]:
            errors.append("layout_not_reachable")

    required = ("report", "schema", "recorder_ok", "actions_executed", "console_errors")
    ok = bool(report) and all(checks.get(k) for k in required)
    if require_frames and not checks.get("frames_captured"):
        ok = False
    # 布局不可达 / 玩家完全卡住 → 判定失败（驱动 Debugger 修复）
    if checks.get("layout_reachable") is False or checks.get("player_moved") is False:
        ok = False
    return {"ok": ok, "checks": checks, "errors": errors}


class PlaytestRunner:
    """驱动一次 playtest：注入录制器 → 跑游戏 → 收报告评分。"""

    def __init__(self, config: Dict[str, Any]):
        from src.engine.godot import _normalize_godot_path, _resolve_env
        from src.engine.godot.session import GodotSession

        godot_cfg = (config or {}).get("godot", {}) or {}
        # 引擎路径收口 GodotSession（M6-06/07：config → env → 自动发现）
        self.editor_path: str = GodotSession.executable(config)
        self.project_path: str = _normalize_godot_path(_resolve_env(
            godot_cfg.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        ))
        pt_cfg = (config or {}).get("playtest", {}) or {}
        self.frames_every: int = int(pt_cfg.get("frames_every", 10))
        self.duration_tail: float = float(pt_cfg.get("duration_tail", 1.0))
        self.timeout: int = int(pt_cfg.get("timeout_seconds", 90))
        self.skip_when_unavailable: bool = bool(pt_cfg.get("skip_when_unavailable", True))
        self.rendering_mode: str = str(pt_cfg.get("rendering_mode", "auto")).lower()
        self.blank_frame_std_threshold: float = float(
            pt_cfg.get("blank_frame_std_threshold", BLANK_FRAME_STD_THRESHOLD)
        )

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
        """执行一次 playtest，返回含评分的完整结果字典（src/core/result 契约）。

        ``findings`` 是 ``errors`` 的历史别名（同一列表对象）。
        """
        out_dir = paths.playtest_dir(project_id, run_id)
        result = new_operation_result(
            "playtest.run",
            project_id=project_id,
            run_id=run_id,
            extra={
                "out_dir": str(out_dir),
                "report": None,
                "console_errors": [],
                "checks": {},
                "frames": [],
                "rendering_mode": None,
            },
        )
        # findings 为 errors 的历史别名（同一列表对象），旧消费方继续可用
        result["findings"] = result["errors"]

        ok_avail, why = self.available()
        if not ok_avail:
            if self.skip_when_unavailable:
                logger.warning("playtest.skipped", reason=why)
                result["skipped"] = True
                result["skip_reason"] = why
                return result
            result["errors"].append(f"环境不可用: {why}")
            return result

        # 1) 落盘动作脚本（可重放的输入证据）
        actions_path = paths.playtest_actions_path(project_id, run_id)
        paths.write_json(actions_path, actions)
        add_artifact(result, "actions", actions_path)

        # 2) 注入录制器（脚本拷贝 + autoload 注册，均幂等）
        inject_error = self._inject_recorder(self.project_path)
        if inject_error:
            result["errors"].append(f"录制器注入失败: {inject_error}")
            return result

        # 3) 逐模式执行；auto=headless 优先，空帧回退窗口模式
        modes = (
            [self.rendering_mode]
            if self.rendering_mode in ("headless", "windowed")
            else ["headless", "windowed"]
        )
        timeout = timeout or self.timeout
        for index, mode in enumerate(modes):
            finished, stderr = self._run_engine_once(result, mode, actions_path, out_dir, scene_path, timeout)
            self._collect_evidence(result, stderr)

            # headless 跑通了但帧证据缺失或为纯色空帧 → 画面不可信，
            # 回退窗口模式重试（dummy 渲染器没有渲染服务器，实弹已验证）
            headless_frames_unusable = (
                not result["frames"]
                or detect_blank_frames(
                    paths.playtest_frames_dir(project_id, run_id),
                    result["frames"],
                    std_threshold=self.blank_frame_std_threshold,
                )["blank"]
            )
            if finished and mode == "headless" and index + 1 < len(modes) and headless_frames_unusable:
                result["warnings"].append(
                    "headless 帧证据为纯色空帧（dummy 渲染器无渲染服务器），回退窗口模式重试"
                )
                logger.warning("playtest.blank_frames_fallback", mode="windowed")
                continue

            # 4) 评分（只评本轮已有证据；若项目带 layout_report 则一并门禁）
            layout_report = None
            try:
                lr_path = Path(self.project_path) / "layout_report.json"
                if lr_path.is_file():
                    import json as _json

                    layout_report = _json.loads(lr_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                layout_report = None
            verdict = evaluate_report(
                result["report"],
                console_errors=result["console_errors"],
                require_frames=False,
                layout_report=layout_report,
            )
            result["ok"] = verdict["ok"]
            result["checks"] = verdict["checks"]
            result["errors"].extend(verdict["errors"])
            break

        logger.info(
            "playtest.finished",
            project_id=project_id, ok=result["ok"], mode=result["rendering_mode"],
            actions=result["checks"].get("actions_executed"),
            frames=result["checks"].get("frames_captured"),
        )
        return result

    # ── 执行与取证 ──────────────────────────────────────────────────────

    def _run_engine_once(
        self,
        result: Dict[str, Any],
        mode: str,
        actions_path: Path,
        out_dir: Path,
        scene_path: Optional[str],
        timeout: int,
    ) -> Tuple[bool, str]:
        """用指定渲染模式启动一次游戏。

        返回 ``(进程是否正常结束, stderr 文本)``；每轮启动前清掉上一轮的
        report.json 与帧目录，避免陈旧证据混入。
        elapsed_seconds / exit_code / rendering_mode 写回 result。
        """
        project_id, run_id = result["project_id"], result["run_id"]
        for stale in (paths.playtest_report_path(project_id, run_id),
                      paths.playtest_frames_dir(project_id, run_id)):
            try:
                if stale.is_file():
                    stale.unlink()
                elif stale.is_dir():
                    shutil.rmtree(stale)
            except OSError:  # noqa: PERF203
                pass

        env = os.environ.copy()
        env["GAMEFORGE_PLAYTEST_ACTIONS"] = str(actions_path.resolve())
        env["GAMEFORGE_PLAYTEST_OUT"] = str(out_dir)
        env["GAMEFORGE_PLAYTEST_FRAME_INTERVAL"] = str(self.frames_every)
        cmd = [self.editor_path, "--path", self.project_path]
        if mode == "headless":
            cmd.insert(1, "--headless")
        if scene_path:
            cmd.append(scene_path)

        timeout = timeout or self.timeout
        from src.engine.godot import godot_user_env

        env = godot_user_env(env)
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace", cwd=self.project_path, env=env,
            )
        except subprocess.TimeoutExpired as e:
            stderr = (e.stderr or b"")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            result["elapsed_seconds"] = round(time.monotonic() - t0, 2)
            result["rendering_mode"] = mode
            result["errors"].append(f"playtest 超时 ({timeout}s)，进程已终止")
            return False, stderr or ""
        except Exception as e:  # noqa: BLE001
            result["elapsed_seconds"] = round(time.monotonic() - t0, 2)
            result["rendering_mode"] = mode
            result["errors"].append(f"playtest 启动失败: {e}")
            return False, ""

        result["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        result["exit_code"] = proc.returncode
        result["rendering_mode"] = mode
        return True, proc.stderr or ""

    def _collect_evidence(self, result: Dict[str, Any], stderr_text: str) -> None:
        """解析控制台错误 + 收 report.json/帧产物清单（超时/失败也尽量收）。"""
        from src.engine.godot.runtime_smoke import _parse_runtime_errors

        project_id, run_id = result["project_id"], result["run_id"]
        result["console_errors"] = _parse_runtime_errors(stderr_text or "")
        report = paths.read_json(paths.playtest_report_path(project_id, run_id))
        if report is None:
            result["errors"].append("录制器未产出 report.json（游戏可能启动即崩溃）")
        result["report"] = report
        if report is not None:
            add_artifact(result, "report", paths.playtest_report_path(project_id, run_id))

        frames_dir = paths.playtest_frames_dir(project_id, run_id)
        if frames_dir.is_dir():
            result["frames"] = sorted(p.name for p in frames_dir.glob("f*.png"))
            if result["frames"]:
                add_artifact(result, "frames_dir", frames_dir)

    # ── 注入 ────────────────────────────────────────────────────────────

    def _inject_recorder(self, project_path: str) -> str:
        """拷贝录制器脚本到项目并注册 autoload（均幂等）。返回错误信息或空串。"""
        try:
            target_dir = os.path.join(project_path, "addons", "gameforge")
            os.makedirs(target_dir, exist_ok=True)
            target_script = os.path.join(target_dir, "playtest_recorder.gd")
            if not os.path.isfile(target_script) or (
                os.path.getmtime(RECORDER_SOURCE) > os.path.getmtime(target_script)
            ):
                # 副本缺失或源码更新过（如录制器 bug 修复）→ 重新拷贝，
                # 否则项目里驻留的旧录制器会让 playtest 行为停留在历史版本
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
