"""真窗口 + mss 截图模块。

为什么不走 Godot viewport API：
  Windows headless dummy renderer 永远拿不到 viewport texture。
  但带窗口模式下 Godot 用 opengl3/vulkan 真渲染，只要窗口可见就有 framebuffer。
  我们用 mss (Python Windows GDI / DXGI) 在屏幕外区域抓图，规避渲染器限制。

调用流程：
  GodotSupervisor.start(project_id, ...)
    → spawn: godot.exe --rendering-driver opengl3 --position 20000,20000 --path project
    → 窗口出现在 (20000, 20000) 屏幕外（用户感知不到）
  每次请求 /api/v1/preview/frame:
    → mss.grab({"left": 20000, "top": 20000, "width": W, "height": H})
    → PIL.Image.frombytes → save_png → bytes
"""

from __future__ import annotations

import io
import os
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()

try:
    import mss
    from PIL import Image

    HAS_MSS = True
except ImportError:
    HAS_MSS = False


# Godot 窗口的位置（与 preview_runner.gd 的 _post_init 一致）
# 把窗口放到屏幕右下角紧贴任务栏上方的小区域 (1216, 770) 320x180，
# 用户不会察觉，但 mss 100% 能抓到虚拟桌面内的像素
WINDOW_X = 1216
WINDOW_Y = 770
WINDOW_W = 320
WINDOW_H = 180


@dataclass
class WindowCapture:
    """单个 Godot 窗口的截图器。线程安全。"""

    project_id: str
    window_x: int = WINDOW_X
    window_y: int = WINDOW_Y
    width: int = WINDOW_W
    height: int = WINDOW_H

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_bytes: Optional[bytes] = None
    _last_capture_at: float = 0.0

    def grab(self) -> Optional[bytes]:
        """抓一帧 PNG，返回 None 表示失败"""
        if not HAS_MSS:
            return None
        with self._lock:
            try:
                with mss.mss() as sct:
                    region = {
                        "left": self.window_x,
                        "top": self.window_y,
                        "width": self.width,
                        "height": self.height,
                    }
                    raw = sct.grab(region)
                    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG", optimize=False)
                    self._last_bytes = buf.getvalue()
                    self._last_capture_at = time.time()
                    return self._last_bytes
            except Exception as e:
                logger.warning("screenshot_gpu.grab_error", project_id=self.project_id, error=str(e))
                return None

    def is_black(self, png_bytes: Optional[bytes]) -> bool:
        """启发式判断 PNG 是否几乎全黑（窗口被遮挡或未渲染）"""
        if not png_bytes or len(png_bytes) < 100:
            return True
        try:
            import io as _io
            img = Image.open(_io.BytesIO(png_bytes)).convert("RGB")
            # 取中心 50x50 像素平均亮度
            cx, cy = img.width // 2, img.height // 2
            crop = img.crop((cx - 25, cy - 25, cx + 25, cy + 25))
            pixels = list(crop.getdata())
            avg = sum(sum(p) for p in pixels) / (len(pixels) * 3)
            return avg < 5  # 全黑 < 5/255
        except Exception:
            return False


def capture_window(project_id: str, width: int = 640, height: int = 360) -> Optional[bytes]:
    """便捷函数：单次截图

    优先用 PrintWindow API（不依赖窗口位置，能抓到 OpenGL/D3D 渲染内容）。
    失败时回退到 mss（要求窗口在虚拟桌面内）。
    """
    # 优先 PrintWindow
    png = capture_godot_window(project_id, width, height)
    if png:
        return png
    # 回退 mss
    wc = WindowCapture(project_id=project_id, width=width, height=height)
    return wc.grab()


def capture_godot_window(project_id: str, width: int = 640, height: int = 360) -> Optional[bytes]:
    """通过 Win32 PrintWindow API 直接抓 Godot 窗口内容（不依赖虚拟桌面位置）。

    这是 mss 方案的替代：当 Godot 窗口被放到虚拟桌面外时，mss 抓不到，
    但 PrintWindow 走 GDI 直接拷窗口 DC 内容。

    Returns:
        PNG bytes 或 None（找不到窗口 / 失败）
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
        )

        # 找出进程名为 Godot_v4.6.3 的窗口（取最大的那个 = 主窗口）
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId

        candidates: List[Tuple[int, int, int]] = []  # (area, hwnd, area)

        def _enum(hwnd, _lparam):
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # 用 GetWindowText 过滤：Godot 进程窗口通常带 "Godot" 或项目名（"GameForge Preview"）
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            # 过滤：(含 Godot/GameForge/Preview) 且 (可见/最小化/被遮挡)
            t = title.lower()
            if not ("godot" in t or "gameforge" in t or "preview" in t):
                return True
            rect = wintypes.RECT()
            try:
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
            except Exception:
                return True
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 100 or h < 100:
                return True
            candidates.append((w * h, hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        if not candidates:
            return None
        candidates.sort(reverse=True)
        hwnd = candidates[0][1]

        # 创建设备上下文 + 内存位图
        hdc_window = user32.GetWindowDC(hwnd)
        if not hdc_window:
            return None
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
            if not hdc_mem:
                return None
            try:
                hbitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
                if not hbitmap:
                    return None
                try:
                    gdi32.SelectObject(hdc_mem, hbitmap)
                    # PW_RENDERFULLCONTENT = 0x00000002 让 OpenGL/D3D 渲染目标也被捕获
                    user32.PrintWindow(hwnd, hdc_mem, 0x00000002)

                    # 从位图读取像素
                    class BITMAPINFOHEADER(ctypes.Structure):
                        _fields_ = [
                            ("biSize", wintypes.DWORD),
                            ("biWidth", wintypes.LONG),
                            ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD),
                            ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG),
                            ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD),
                            ("biClrImportant", wintypes.DWORD),
                        ]

                    class BITMAPINFO(ctypes.Structure):
                        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

                    bmi = BITMAPINFO()
                    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bmi.bmiHeader.biWidth = width
                    bmi.bmiHeader.biHeight = -height  # 负值 = top-down
                    bmi.bmiHeader.biPlanes = 1
                    bmi.bmiHeader.biBitCount = 32
                    bmi.bmiHeader.biCompression = 0

                    buf = (ctypes.c_ubyte * (width * height * 4))()
                    gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buf, ctypes.byref(bmi), 0)

                    # 转 BGRA → RGB
                    img = Image.frombuffer("RGBA", (width, height), bytes(buf), "raw", "BGRA", 0, 1)
                    img = img.convert("RGB")
                    out = io.BytesIO()
                    img.save(out, format="PNG", optimize=False)
                    return out.getvalue()
                finally:
                    gdi32.DeleteObject(hbitmap)
            finally:
                gdi32.DeleteDC(hdc_mem)
        finally:
            user32.ReleaseDC(hwnd, hdc_window)
    except Exception as e:
        logger.warning("capture_godot_window.error", error=str(e))
        return None


def check_mss_available() -> bool:
    """mss 是否可用"""
    return HAS_MSS


# ═══════════════════════════════════════════════════════════════
# Godot 窗口查找（备用方案：通过 Windows API 找 Godot 窗口句柄）
# ═══════════════════════════════════════════════════════════════


def find_godot_window(process_name_prefix: str = "Godot") -> Optional[Dict[str, int]]:
    """通过 Win32 API 找 Godot 窗口，返回 {left, top, width, height}"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        results = []

        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if process_name_prefix.lower() in title.lower():
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                if w > 100 and h > 100:
                    results.append({
                        "left": rect.left,
                        "top": rect.top,
                        "width": w,
                        "height": h,
                        "hwnd": hwnd,
                    })
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        # 优先选有标题且尺寸匹配的
        return results[0] if results else None
    except Exception as e:
        logger.warning("find_godot_window.error", error=str(e))
        return None