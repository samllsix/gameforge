"""GameForge - Godot 运行时冒烟测试。

P0-2：现有流程只校验语法（compile_project/check_scripts），不验证"能不能跑"。
这里补一个独立模块：用 `godot --headless --quit-after N` 跑场景 N 帧后退出，
解析 stderr 中的运行时错误，作为"可运行"信号。

依赖：godot 可执行文件、project.godot、`scene_path`（res://scenes/X.tscn）。
不引入任何新引擎/素材依赖。
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# 匹配 Godot 4 headless stderr 里的运行时错误关键字
# 注意：避免误报 - "WARNING" / "DEPRECATED" 不算错误
_RUNTIME_ERROR_PATTERNS = [
    re.compile(r"SCRIPT ERROR"),
    re.compile(r"RuntimeError"),
    re.compile(r"Parser Error"),
    re.compile(r"Failed to (?:load|parse|find)"),
    re.compile(r"^\s*ERROR\s*:", re.MULTILINE),
    re.compile(r"Invalid call\. Nonexistent function"),
    re.compile(r"Invalid get index"),
    re.compile(r"Cannot find class"),
]

# 已知无害噪音（headless 无窗口、下采样等）
_NOISE_PATTERNS = [
    re.compile(r"ALSA lib", re.IGNORECASE),
    re.compile(r"Xlib.*extension", re.IGNORECASE),
    re.compile(r"MESA-LOADER", re.IGNORECASE),
    re.compile(r"Initialize GLEW"),
    re.compile(r"OpenGL ES .* warning"),
    re.compile(r"DisplayServer.*no main loop", re.IGNORECASE),
]


@dataclass
class RuntimeSmokeResult:
    """冒烟测试结果"""

    runnable: bool
    exit_code: int
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    frame_count: int = 0
    output: str = ""
    elapsed_seconds: float = 0.0
    scene_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runnable": self.runnable,
            "exit_code": self.exit_code,
            "errors": self.errors,
            "warnings": self.warnings,
            "frame_count": self.frame_count,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "scene_path": self.scene_path,
        }


def _strip_noise(stderr: str) -> str:
    """去掉已知噪音（ALSA/Xlib/headless 渲染警告）"""
    lines = stderr.splitlines()
    keep = []
    for ln in lines:
        if any(p.search(ln) for p in _NOISE_PATTERNS):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _parse_runtime_errors(stderr: str) -> List[Dict[str, Any]]:
    """从 stderr 提取运行时错误。"""
    text = _strip_noise(stderr)
    errs: List[Dict[str, Any]] = []
    seen: set = set()
    for pat in _RUNTIME_ERROR_PATTERNS:
        for m in pat.finditer(text):
            key = (m.group(0), m.start())
            if key in seen:
                continue
            seen.add(key)
            # 抽取上下文（错误行 + 下一行）
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 400)
            snippet = text[start:end].strip()
            errs.append({
                "pattern": m.group(0),
                "snippet": snippet[:500],
            })
    return errs


def _parse_frame_count(stdout: str) -> int:
    """尽量从输出里抓帧数（场景会自己打 print）。"""
    m = re.search(r"frames\s*[:=]\s*(\d+)", stdout, re.IGNORECASE)
    return int(m.group(1)) if m else 0


class GodotRuntimeSmoke:
    """Godot 运行时冒烟测试。

    用法：
        smoke = GodotRuntimeSmoke(config)
        result = smoke.run_scene(scene_path="res://scenes/Main.tscn", frames=60)
        if result.runnable:
            ...  # 通过
        else:
            ...  # 把 result.errors 喂给 DebuggerAgent
    """

    DEFAULT_FRAMES = 60   # 60 帧 ≈ 1 秒 @ 60 FPS，足够暴露 _ready/_process 错误
    DEFAULT_TIMEOUT = 30  # 秒

    def __init__(self, config: Dict[str, Any]):
        from src.engine.godot import _normalize_godot_path, _resolve_env
        godot_cfg = (config or {}).get("godot", {}) or {}
        self.editor_path: str = _normalize_godot_path(_resolve_env(
            godot_cfg.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        ))
        self.project_path: str = _normalize_godot_path(_resolve_env(
            godot_cfg.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        ))
        smoke_cfg = (config or {}).get("runtime_smoke", {}) or {}
        self.frames: int = int(smoke_cfg.get("frames", self.DEFAULT_FRAMES))
        self.timeout: int = int(smoke_cfg.get("timeout_seconds", self.DEFAULT_TIMEOUT))
        # 没装 Godot 时静默降级为"已跳过"
        self.skip_when_unavailable: bool = bool(
            smoke_cfg.get("skip_when_unavailable", True)
        )

    def available(self) -> Tuple[bool, str]:
        """检查 Godot 是否可用 + scene 路径是否存在。"""
        if not self.editor_path:
            return False, "未配置 godot.editor_path / GODOT_EDITOR_PATH"
        if not os.path.isfile(self.editor_path):
            return False, f"Godot 可执行文件不存在: {self.editor_path}"
        if not self.project_path or not os.path.isdir(self.project_path):
            return False, f"Godot 项目目录不存在: {self.project_path}"
        return True, "OK"

    def run_scene(
        self,
        scene_path: str,
        frames: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> RuntimeSmokeResult:
        """跑场景 N 帧后退出，捕获运行时错误。

        Args:
            scene_path: res:// 路径或绝对路径
            frames: 跑多少帧后让 Godot 退出（用 --quit-after）
            timeout: 超时秒数

        Returns:
            RuntimeSmokeResult，含 runnable / errors / frame_count
        """
        frames = frames or self.frames
        timeout = timeout or self.timeout
        available, why = self.available()
        if not available:
            if self.skip_when_unavailable:
                logger.warning("runtime_smoke.skipped", reason=why)
                return RuntimeSmokeResult(
                    runnable=True,  # 降级为通过，避免误判
                    exit_code=0,
                    errors=[{"pattern": "SKIPPED", "snippet": why}],
                    scene_path=scene_path,
                )
            return RuntimeSmokeResult(
                runnable=False, exit_code=-1,
                errors=[{"pattern": "ENV", "snippet": why}],
                scene_path=scene_path,
            )

        cmd = [
            self.editor_path,
            "--headless",
            "--quit-after", str(frames),
            "--path", self.project_path,
            scene_path,
        ]
        import time
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8", errors="replace",
                cwd=self.project_path,
            )
        except subprocess.TimeoutExpired as e:
            elapsed = time.monotonic() - t0
            return RuntimeSmokeResult(
                runnable=False,
                exit_code=-1,
                errors=[{
                    "pattern": "TIMEOUT",
                    "snippet": f"场景 {scene_path} 跑 {frames} 帧超时 ({timeout}s)",
                }],
                output=str(e),
                elapsed_seconds=elapsed,
                scene_path=scene_path,
            )
        except Exception as e:
            return RuntimeSmokeResult(
                runnable=False,
                exit_code=-2,
                errors=[{"pattern": "EXEC", "snippet": str(e)}],
                scene_path=scene_path,
            )

        elapsed = time.monotonic() - t0
        stderr = proc.stderr or ""
        stdout = proc.stdout or ""
        errs = _parse_runtime_errors(stderr)
        warnings = [ln for ln in stderr.splitlines() if "WARNING" in ln.upper()]
        frame_count = _parse_frame_count(stdout)

        # 通过条件：进程退出码 0 + stderr 无运行时错误
        runnable = proc.returncode == 0 and len(errs) == 0
        return RuntimeSmokeResult(
            runnable=runnable,
            exit_code=proc.returncode,
            errors=errs,
            warnings=warnings,
            frame_count=frame_count,
            output=stdout + "\n" + stderr,
            elapsed_seconds=elapsed,
            scene_path=scene_path,
        )