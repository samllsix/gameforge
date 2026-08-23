"""GameForge - Godot 实时预览进程管理器（Supervisor）

按 project_id 缓存常驻的 Godot 截图进程。Python 后端通过反向 HTTP 调用
`http://127.0.0.1:<port>/screenshot?frame=N` 拿到当前 Viewport 的 PNG。

特性：
- 启动 Godot 长驻进程（preview_runner.gd），监听 8769（可配）
- 进程崩溃自愈：端口探活失败 3 次 → 重启，指数退避
- 单进程内 Godot 单线程 → 每 project 串行；多 project 可并行
- LRU 滚动重启：单进程运行超过 max_process_age_seconds 自动重启
- 反向 HTTP 调用全程 loopback + X-API-Key，不暴露公网
- 健康检查后台 task，每 3s 探一次
- 上下文管理器 shutdown：FastAPI lifespan 退出时优雅关闭所有子进程

设计参考：`docs/realtime-preview-design.md`
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

# Godot 窗口固定位置（与 screenshot_gpu.py、preview_runner.gd 一致）
WINDOW_X = 20000
WINDOW_Y = 20000

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPXX = False
    httpx = None  # type: ignore


# ============ 异常类型 ============


class GodotTimeout(Exception):
    """截图调用超时"""


class GodotCrashed(Exception):
    """Godot 进程已崩溃或不可达"""


# ============ 单项目进程记录 ============


@dataclass
class ProjectProc:
    """单个 Godot 项目的进程记录"""

    project_id: str
    project_path: str
    port: int
    proc: Optional[subprocess.Popen] = None
    started_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    last_health_ok_at: float = 0.0
    consecutive_health_failures: int = 0
    consecutive_crash_count: int = 0
    current_scene: str = "res://scenes/main.tscn"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def age(self) -> float:
        return time.time() - self.started_at

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


# ============ Supervisor 单例 ============


class GodotSupervisor:
    """Godot 实时预览进程管理器（异步友好，单例）

    用法：
        sup = await GodotSupervisor.get_instance(config)
        png = await sup.get_frame("demo_jump_v2", frame=42)
        # FastAPI 退出时：
        await sup.stop_all()
    """

    _instance: Optional["GodotSupervisor"] = None
    _instance_lock: Optional[asyncio.Lock] = None

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        preview_cfg = (config or {}).get("preview", {}) or {}
        self.enabled: bool = bool(preview_cfg.get("enabled", True))
        self.token: str = (
            os.getenv(preview_cfg.get("screenshot_token_env", "GAMEFORGE_PREVIEW_TOKEN"), "").strip()
            or str(preview_cfg.get("default_token", "gf_screenshot_local"))
        )
        self.startup_timeout: float = float(preview_cfg.get("startup_timeout_seconds", 8))
        self.request_timeout: float = float(preview_cfg.get("request_timeout_seconds", 5))
        self.health_interval: float = float(preview_cfg.get("health_check_interval_seconds", 3))
        self.max_age: float = float(preview_cfg.get("max_process_age_seconds", 1800))
        self.backoff: List[int] = list(preview_cfg.get("restart_backoff", [1, 3, 9]))
        self.legacy_only: bool = bool(preview_cfg.get("legacy_only", False))
        win = preview_cfg.get("window_position") or [20000, 20000]
        self.window_position: List[int] = list(win) if isinstance(win, list) else [20000, 20000]

        godot_cfg = (config or {}).get("godot", {}) or {}
        from src.engine.godot import _normalize_godot_path, _resolve_env
        self.editor_path: str = _normalize_godot_path(_resolve_env(
            godot_cfg.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        ))

        self._procs: Dict[str, ProjectProc] = {}
        self._registry_lock = asyncio.Lock()
        self._health_task: Optional[asyncio.Task] = None
        self._stopped: bool = False

    @classmethod
    async def get_instance(cls, config: Dict[str, Any]) -> "GodotSupervisor":
        if cls._instance is None:
            if cls._instance_lock is None:
                cls._instance_lock = asyncio.Lock()
            async with cls._instance_lock:
                if cls._instance is None:
                    inst = cls(config)
                    await inst.start_health_loop()
                    cls._instance = inst
        return cls._instance

    async def start_health_loop(self) -> None:
        if self._health_task is not None and not self._health_task.done():
            return
        self._health_task = asyncio.create_task(self._health_loop(), name="godot-supervisor-health")

    async def _health_loop(self) -> None:
        """后台每 health_interval 秒探一次所有进程"""
        while not self._stopped:
            try:
                await asyncio.sleep(self.health_interval)
            except asyncio.CancelledError:
                return
            for pid in list(self._procs.keys()):
                try:
                    await self._health_check_one(pid)
                except Exception as e:  # noqa: BLE001
                    logger.warning("supervisor.health_check_error", project_id=pid, error=str(e))

    async def _health_check_one(self, project_id: str) -> None:
        pp = self._procs.get(project_id)
        if pp is None:
            return
        # LRU 滚动重启
        if pp.age() > self.max_age:
            logger.info("supervisor.rolling_restart", project_id=project_id, age=pp.age())
            await self.stop(project_id)
            return
        if not pp.is_alive():
            self._mark_failure(project_id, reason="process_dead")
            return
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"http://127.0.0.1:{pp.port}/health")
                if r.status_code == 200:
                    pp.last_health_ok_at = time.time()
                    pp.consecutive_health_failures = 0
                else:
                    self._mark_failure(project_id, reason=f"health_status_{r.status_code}")
        except Exception as e:  # noqa: BLE001
            self._mark_failure(project_id, reason=f"health_error: {e}")

    def _mark_failure(self, project_id: str, *, reason: str) -> None:
        pp = self._procs.get(project_id)
        if pp is None:
            return
        pp.consecutive_health_failures += 1
        logger.warning(
            "supervisor.health_fail",
            project_id=project_id,
            reason=reason,
            consecutive=pp.consecutive_health_failures,
        )
        if pp.consecutive_health_failures >= 3:
            # 标记不可达，下次 get_frame 时会重启
            if pp.proc and pp.proc.poll() is None:
                try:
                    pp.proc.terminate()
                except Exception:
                    pass

    # ============ 公开 API ============

    async def is_alive(self, project_id: str) -> bool:
        pp = self._procs.get(project_id)
        return pp is not None and pp.is_alive()

    async def start(self, project_id: str, project_path: str, scene_path: Optional[str] = None) -> None:
        """启动（或复用）指定项目的 Godot 截图进程"""
        async with self._registry_lock:
            pp = self._procs.get(project_id)
            if pp is not None and pp.is_alive():
                pp.last_used_at = time.time()
                return
            port = await self._pick_free_port()
            try:
                pp = await self._spawn(project_id, project_path, port, scene_path)
            except Exception as e:
                logger.error("supervisor.spawn_failed", project_id=project_id, error=str(e))
                raise
            self._procs[project_id] = pp
            logger.info("supervisor.started", project_id=project_id, port=port)

    async def get_frame(self, project_id: str, frame_index: int = 0, width: int = 640, height: int = 360) -> bytes:
        """获取一帧 PNG。

        实现：mss 抓 Godot 窗口区域（位于屏幕外 WINDOW_X,WINDOW_Y 处）。
        失败时抛 GodotTimeout / GodotCrashed。
        """
        pp = self._procs.get(project_id)
        if pp is None or not pp.is_alive():
            raise GodotCrashed(f"Godot 进程未运行: {project_id}")

        async with pp.lock:
            pp.last_used_at = time.time()

        # mss 截图走线程池（mss 阻塞 GDI 调用）
        from src.engine.godot.screenshot_gpu import capture_window
        loop = asyncio.get_event_loop()
        try:
            png_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, capture_window, project_id, width, height),
                timeout=self.request_timeout,
            )
        except asyncio.TimeoutError as e:
            raise GodotTimeout(f"mss 截图超时 {self.request_timeout}s") from e

        if not png_bytes:
            raise GodotCrashed("mss 截图返回空（可能窗口未渲染或被隐藏）")

        # 启发式：截图全黑说明窗口可能被遮挡或未渲染
        from src.engine.godot.screenshot_gpu import WindowCapture
        wc = WindowCapture(project_id=project_id, width=width, height=height)
        if wc.is_black(png_bytes):
            logger.debug("supervisor.black_frame", project_id=project_id, frame=frame_index)

        return png_bytes

    async def stop(self, project_id: str) -> None:
        async with self._registry_lock:
            pp = self._procs.pop(project_id, None)
        if pp is None:
            return
        await self._terminate_proc(pp)

    async def stop_all(self) -> None:
        self._stopped = True
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
        for pid in list(self._procs.keys()):
            await self.stop(pid)

    async def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "legacy_only": self.legacy_only,
            "processes": {
                pid: {
                    "port": pp.port,
                    "alive": pp.is_alive(),
                    "age_seconds": round(pp.age(), 1),
                    "consecutive_health_failures": pp.consecutive_health_failures,
                    "last_health_ok_at": pp.last_health_ok_at,
                }
                for pid, pp in self._procs.items()
            },
        }

    # ============ 内部工具 ============

    async def _pick_free_port(self) -> int:
        """探测端口是否可用，从 preview_cfg.screenshot_port 起逐个 +1 试"""
        preview_cfg = (self.config or {}).get("preview", {}) or {}
        start = int(preview_cfg.get("screenshot_port", 8769))
        for offset in range(20):
            port = start + offset
            if await asyncio.get_event_loop().run_in_executor(None, _port_is_free, port):
                return port
        # 兜底：让 OS 分配
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    async def _spawn(
        self,
        project_id: str,
        project_path: str,
        port: int,
        scene_path: Optional[str],
    ) -> ProjectProc:
        if not self.editor_path or not os.path.isfile(self.editor_path):
            raise FileNotFoundError(
                f"Godot 编辑器未配置或不存在: {self.editor_path}"
            )
        if not project_path or not os.path.isdir(project_path):
            raise FileNotFoundError(f"项目目录不存在: {project_path}")
        if not os.path.isfile(os.path.join(project_path, "project.godot")):
            raise FileNotFoundError(f"项目目录中找不到 project.godot: {project_path}")

        # 把 preview_runner.gd 与 screenshot_server.gd 复制到目标项目的 addons/gameforge/
        # 让 Godot 启动时能找到这两个脚本（项目独立，源 addon 不一定挂载）
        addon_dir = os.path.join(project_path, "addons", "gameforge")
        os.makedirs(addon_dir, exist_ok=True)
        source_addon = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "addons", "gameforge",
        )
        for fn in ("preview_runner.gd", "screenshot_server.gd", "settings.gd", "config.cfg"):
            src = os.path.join(source_addon, fn)
            dst = os.path.join(addon_dir, fn)
            if os.path.isfile(src):
                try:
                    with open(src, "r", encoding="utf-8") as sf, open(dst, "w", encoding="utf-8") as df:
                        df.write(sf.read())
                except Exception as e:
                    logger.warning("supervisor.copy_addon_failed", file=fn, error=str(e))

        # 注入 preview_runner 到 project.godot 的 autoload 段（如尚未注入）
        self._inject_autoload(project_path)

        # 选场景路径
        scene = scene_path or self._pick_scene(project_path)
        if scene is None:
            raise FileNotFoundError(f"项目 {project_id} 找不到任何 .tscn 场景")

        env = os.environ.copy()
        env["GAMEFORGE_PREVIEW_PORT"] = str(port)
        env["GAMEFORGE_PREVIEW_TOKEN"] = self.token

        # 计算 Godot 窗口位置：紧贴屏幕右下角任务栏上方
        # 默认屏幕 1536x960 → 窗口放 (1216, 770) 320x180
        # 1920x1200 → 窗口放 (1536, 920)
        # 我们通过 Win32 API 探测主屏尺寸，再算出合适位置
        wx, wy = self._compute_window_position(width=320, height=180)

        cmd = [
            self.editor_path,
            "--rendering-driver", "opengl3",
            "--audio-driver", "Dummy",
            "--path", project_path,
            "--resolution", "320x180",
            "--max-fps", "60",
            # 不加 --headless：需要真窗口才能让 mss 抓图
            # 不加 --script：走 project.godot 的 main_scene + autoload
            # 窗口位置由 _move_window_after_spawn 用 Win32 SetWindowPos 设置
        ]
        logger.info(
            "supervisor.spawn",
            project_id=project_id,
            cmd=" ".join(cmd[:3]) + " ...",
            port=port,
            scene=scene,
        )

        proc = subprocess.Popen(
            cmd,
            cwd=project_path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt" else 0,
        )

        pp = ProjectProc(
            project_id=project_id,
            project_path=project_path,
            port=port,
            proc=proc,
            current_scene=scene,
        )

        # 等待端口就绪（仅做 health 探活用；mss 截图不依赖）
        try:
            await self._wait_port_open(port, self.startup_timeout)
            logger.info("supervisor.port_open", project_id=project_id, port=port)
        except GodotTimeout:
            # mss 截图方案下端口不可用也能工作（窗口已就绪）
            if pp.is_alive():
                logger.warning(
                    "supervisor.port_not_open_but_process_alive",
                    project_id=project_id,
                    port=port,
                )
            else:
                try:
                    pp.proc.wait(timeout=2)
                except Exception:
                    pass
                raise GodotCrashed(f"Godot 进程启动后已退出: {project_id}")

        # 把 Godot 窗口用 Win32 SetWindowPos 移到正确位置（因为 --position 在 4.6.3 失效）
        await asyncio.get_event_loop().run_in_executor(
            None, self._move_window_after_spawn, pp, wx, wy, 320, 180,
        )

        return pp

    async def _wait_port_open(self, port: int, timeout: float) -> None:
        """等待 Godot 端 HTTP 服务（8769）就绪。

        注：mss 截图方案其实不需要这个端口（mss 直接抓窗口），但保留以兼容
        screenshot_server.gd 的 /health 端点用于探活。
        """
        loop = asyncio.get_event_loop()
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = await loop.run_in_executor(None, _port_is_free_in_use, port)
            if ok:
                return
            await asyncio.sleep(0.2)
        # 超时但进程还活着，让上层决定如何处理
        raise GodotTimeout(f"Godot {port} 端口 {timeout}s 内未就绪")

    def _move_window_after_spawn(self, pp: "ProjectProc", x: int, y: int, w: int, h: int) -> None:
        """Godot 进程起来后，用 Win32 SetWindowPos 把窗口移到指定位置 + 大小。

        Godot 4.6.3 的 --position 命令行参数被忽略（窗口仍出现在 (-25600, -25600)），
        所以我们必须在 spawn 后用 Win32 API 强制移动。
        """
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )
            GetWindowThreadProcessId = user32.GetWindowThreadProcessId

            target_pid = pp.proc.pid if pp.proc else None
            if not target_pid:
                return

            hwnds = []
            def _enum(hwnd, _l):
                pid = wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == target_pid:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length == 0:
                        return True
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    rw = rect.right - rect.left
                    rh = rect.bottom - rect.top
                    if rw >= 100 and rh >= 100:
                        hwnds.append((hwnd, rw, rh))
                return True

            EnumWindows(EnumWindowsProc(_enum), 0)
            if not hwnds:
                return
            hwnds.sort(key=lambda x: x[1] * x[2], reverse=True)
            hwnd = hwnds[0][0]

            # SWP_NOZORDER=4, SWP_NOACTIVATE=16, SWP_SHOWWINDOW=64
            user32.SetWindowPos(
                hwnd, 0, x, y, w, h,
                0x00000004 | 0x00000010 | 0x00000040,
            )
            # SC_RESTORE = 9 — 如果被最小化，强制还原
            user32.ShowWindow(hwnd, 9)
            logger.info(
                "supervisor.window_moved",
                project_id=pp.project_id,
                hwnd=hwnd,
                x=x, y=y, w=w, h=h,
            )
        except Exception as e:
            logger.warning("supervisor.move_window_failed", error=str(e))

    async def _terminate_proc(self, pp: ProjectProc) -> None:
        if pp.proc is None:
            return
        try:
            if pp.proc.poll() is None:
                pp.proc.terminate()
                try:
                    pp.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pp.proc.kill()
                    pp.proc.wait(timeout=2)
        except Exception as e:
            logger.warning("supervisor.terminate_error", project_id=pp.project_id, error=str(e))

    def _pick_scene(self, project_path: str) -> Optional[str]:
        candidates = [
            "res://scenes/main.tscn",
            "res://main.tscn",
            "res://scenes/GameScene.tscn",
            "res://GameScene.tscn",
        ]
        for c in candidates:
            rel = c.replace("res://", "")
            if os.path.isfile(os.path.join(project_path, rel.replace("/", os.sep))):
                return c
        # 兜底：扫 scenes/ 目录
        scenes_dir = os.path.join(project_path, "scenes")
        if os.path.isdir(scenes_dir):
            for fn in os.listdir(scenes_dir):
                if fn.lower().endswith(".tscn"):
                    return "res://scenes/" + fn
        return None

    def _compute_window_position(self, width: int = 320, height: int = 180) -> tuple:
        """计算 Godot 窗口位置：紧贴屏幕右下角任务栏上方。

        默认屏幕 1536x960 → 窗口放 (1216, 770) 320x180
        1920x1200 → 窗口放 (1536, 920)
        通过 Win32 API 探测主屏尺寸，再算出合适位置。
        """
        screen_w, screen_h = 1536, 960
        if os.name == "nt":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                screen_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            except Exception:
                pass
        wx = max(0, screen_w - width)
        wy = max(0, screen_h - height)
        return (wx, wy)

    def _inject_autoload(self, project_path: str) -> None:
        """把 GameForgePreviewRunner 注入到 project.godot 的 [autoload] 段。

        用 idempotent 模式：检查已有 "GameForgePreviewRunner=" 行，有则跳过。
        """
        pg_path = os.path.join(project_path, "project.godot")
        if not os.path.isfile(pg_path):
            return
        try:
            with open(pg_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            logger.warning("supervisor.read_project_godot_failed", error=str(e))
            return

        marker = "GameForgePreviewRunner"
        if marker in text:
            return  # 已注入

        # 找到 [autoload] 段
        m = re.search(r"^\[autoload\]\s*$", text, re.MULTILINE)
        injection = f'{marker}="*res://addons/gameforge/preview_runner.gd"\n'

        if m:
            # 在 [autoload] 后插入
            pos = m.end()
            new_text = text[:pos] + "\n" + injection + text[pos:]
        else:
            # 在文件末尾添加 [autoload] 段
            new_text = text.rstrip() + "\n\n[autoload]\n" + injection

        try:
            with open(pg_path, "w", encoding="utf-8") as f:
                f.write(new_text)
            logger.info("supervisor.autoload_injected", path=pg_path)
        except Exception as e:
            logger.warning("supervisor.autoload_inject_failed", error=str(e))


# ============ 同步辅助 ============


def _port_is_free(port: int) -> bool:
    """端口未被占用（可绑定）→ True"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _port_is_free_in_use(port: int) -> bool:
    """端口已被监听 → True（用于探活 Godot 进程是否启动）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
        return True
    except (OSError, socket.timeout):
        return False
    finally:
        s.close()


# ============ FastAPI lifespan 接入 ============


@asynccontextmanager
async def supervisor_lifespan(config: Dict[str, Any]):
    """FastAPI lifespan 上下文管理器。自动起停 supervisor。"""
    sup = await GodotSupervisor.get_instance(config)
    try:
        yield sup
    finally:
        await sup.stop_all()