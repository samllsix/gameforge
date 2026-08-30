"""GameForge - 导出套件

write_project 时给生成的项目带上 export_presets.cfg（Web + Windows Desktop），
发布时用 export_project 调 Godot headless 导出。

Web 导出产物需要 COOP/COEP 响应头（thread_support），由 API 的 /play 路由负责。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()

# Web + Windows 双预设；export_path 相对项目根
EXPORT_PRESETS_CFG = """[preset.0]

name="Web"
platform="Web"
runnable=true
advanced_options=false
dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path="export/web/index.html"
patches=PackedStringArray()
encryption_include_filters=""
encryption_exclude_filters=""
seed=0
encrypt_pck=false
encrypt_directory=false
script_export_mode=2

[preset.0.options]

custom_template/debug=""
custom_template/release=""
variant/extensions_support=false
variant/thread_support=true
vram_texture_compression/for_desktop=true
vram_texture_compression/for_mobile=false
html/export_icon=true
html/custom_html_shell=""
html/head_include=""
html/canvas_resize_policy=2
html/focus_canvas_on_start=true
html/experimental_virtual_keyboard=false
progressive_web_app/enabled=false

[preset.1]

name="Windows Desktop"
platform="Windows Desktop"
runnable=true
advanced_options=false
dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path="export/windows/GameForge.exe"
patches=PackedStringArray()
encryption_include_filters=""
encryption_exclude_filters=""
seed=0
encrypt_pck=false
encrypt_directory=false
script_export_mode=2

[preset.1.options]

custom_template/debug=""
custom_template/release=""
debug/export_console_wrapper=1
binary_format/embed_pck=true
texture_format/s3tc_bptc=true
texture_format/etc2_astc=false
binary_format/architecture="x86_64"
application/icon=""
application/file_version=""
application/product_version=""
application/company_name="GameForge"
application/product_name="GameForge Game"
application/file_description=""
application/copyright=""
application/trademarks=""
"""


def write_export_presets(project_path: str) -> str:
    """写入 export_presets.cfg（已存在则跳过，保留用户自定义）。"""
    out = os.path.join(project_path, "export_presets.cfg")
    if not os.path.isfile(out):
        with open(out, "w", encoding="utf-8") as f:
            f.write(EXPORT_PRESETS_CFG)
    return out


def ensure_imported(project_path: str, editor_path: str, timeout: float = 180.0) -> bool:
    """headless 导入项目资源（新 PNG/WAV 首次使用前必须 import，否则运行时加载失败）。

    幂等：已有 .godot 缓存时 Godot 快速跳过。返回是否成功。
    """
    try:
        proc = subprocess.run(
            [editor_path, "--headless", "--import", "--path", project_path],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode == 0
    except Exception as e:  # noqa: BLE001
        logger.warning("export_kit.ensure_imported_failed", error=str(e))
        return False


def export_project(
    project_path: str,
    editor_path: str,
    preset_name: str = "Web",
    timeout: float = 300.0,
) -> Dict[str, Any]:
    """headless 导出。返回 {ok, out_path, stderr_tail}。

    要求导出模板已安装（%APPDATA%/Godot/export_templates/<ver>）。
    """
    presets = os.path.join(project_path, "export_presets.cfg")
    if not os.path.isfile(presets):
        write_export_presets(project_path)

    if preset_name == "Windows Desktop":
        out_rel = os.path.join("export", "windows", "GameForge.exe")
    else:
        out_rel = os.path.join("export", "web", "index.html")
    out_path = os.path.join(project_path, out_rel)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    cmd = [
        editor_path,
        "--headless",
        "--path", project_path,
        "--export-release", preset_name, out_rel,
    ]
    logger.info("export_kit.export_start", preset=preset_name, out=out_rel)
    try:
        proc = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        ok = proc.returncode == 0 and os.path.isfile(out_path)
        tail = (proc.stderr or "")[-800:]
        if not ok:
            logger.warning("export_kit.export_failed", preset=preset_name, tail=tail[-200:])
        else:
            logger.info("export_kit.export_done", preset=preset_name, out=out_rel)
        return {"ok": ok, "out_path": out_path if ok else None, "stderr_tail": tail, "returncode": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out_path": None, "stderr_tail": f"导出超时（>{timeout}s）", "returncode": -1}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "out_path": None, "stderr_tail": str(e), "returncode": -1}
