"""GameForge - 导出套件

write_project 时给生成的项目带上 export_presets.cfg（Web + Windows Desktop），
发布时用 export_project 调 Godot headless 导出。

Web 导出产物需要 COOP/COEP 响应头（thread_support），由 API 的 /play 路由负责。
"""

from __future__ import annotations

import os
import subprocess
import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
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


def ensure_imported(
    project_path: str, editor_path: str, timeout: float = 180.0
) -> bool:
    """headless 导入项目资源（新 PNG/WAV 首次使用前必须 import，否则运行时加载失败）。

    幂等：已有 .godot 缓存时 Godot 快速跳过。返回是否成功。
    """
    try:
        from src.engine.godot import godot_user_env

        proc = subprocess.run(
            [editor_path, "--headless", "--import", "--path", project_path],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=godot_user_env(),
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
        "--path",
        project_path,
        "--export-release",
        preset_name,
        out_rel,
    ]
    logger.info("export_kit.export_start", preset=preset_name, out=out_rel)
    try:
        from src.engine.godot import godot_user_env

        proc = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=godot_user_env(),
        )
        ok = proc.returncode == 0 and os.path.isfile(out_path)
        tail = (proc.stderr or "")[-800:]
        if not ok:
            logger.warning(
                "export_kit.export_failed", preset=preset_name, tail=tail[-200:]
            )
        else:
            logger.info("export_kit.export_done", preset=preset_name, out=out_rel)
        return {
            "ok": ok,
            "out_path": out_path if ok else None,
            "stderr_tail": tail,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "out_path": None,
            "stderr_tail": f"导出超时（>{timeout}s）",
            "returncode": -1,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "out_path": None, "stderr_tail": str(e), "returncode": -1}


def export_web_build(
    project_path: str,
    editor_path: str,
    timeout: float = 300.0,
) -> Dict[str, Any]:
    """导出不可变 Web 构建，并原子发布当前构建清单。

    失败时不会触碰 ``current.json``，因此客户端始终可继续访问最近一次成功构建。
    """
    source_root = Path(project_path).resolve()
    builds_root = source_root / ".gameforge" / "builds" / "web"
    build_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:10]
    )
    build_dir = builds_root / build_id
    builds_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{build_id}-", dir=str(builds_root)))
    output_path = staging_dir / "index.html"
    try:
        presets = source_root / "export_presets.cfg"
        if not presets.is_file():
            write_export_presets(str(source_root))
        cmd = [
            editor_path,
            "--headless",
            "--path",
            str(source_root),
            "--export-release",
            "Web",
            str(output_path),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(source_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stderr_tail = (proc.stderr or "")[-800:]
        if proc.returncode != 0 or not output_path.is_file():
            return {
                "ok": False,
                "build_id": build_id,
                "out_path": None,
                "stderr_tail": stderr_tail,
                "returncode": proc.returncode,
            }

        # 目录改名在同一文件系统内完成，构建目录对读取者要么不存在、要么完整存在。
        os.replace(str(staging_dir), str(build_dir))
        files = []
        for file_path in sorted(build_dir.rglob("*")):
            if file_path.is_file():
                data = file_path.read_bytes()
                files.append(
                    {
                        "path": file_path.relative_to(build_dir).as_posix(),
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
        manifest = {
            "build_id": build_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entry_path": "index.html",
            "target": "Web",
            "files": files,
        }
        manifest_path = build_dir / "manifest.json"
        _atomic_write_json(manifest_path, manifest)
        # current.json 是唯一可变指针；os.replace 保证发布动作原子化。
        _atomic_write_json(builds_root / "current.json", manifest)
        return {
            "ok": True,
            "build_id": build_id,
            "out_path": str(build_dir / "index.html"),
            "manifest": manifest,
            "stderr_tail": stderr_tail,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "build_id": build_id,
            "out_path": None,
            "stderr_tail": f"导出超时（>{timeout}s）",
            "returncode": -1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("export_kit.versioned_web_export_failed", error=str(exc))
        return {
            "ok": False,
            "build_id": build_id,
            "out_path": None,
            "stderr_tail": str(exc),
            "returncode": -1,
        }
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def get_web_build(
    project_path: str, build_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """读取指定或当前 Web 构建清单，不创建任何文件。"""
    root = Path(project_path).resolve() / ".gameforge" / "builds" / "web"
    manifest_path = (
        root / (build_id or "current") / "manifest.json"
        if build_id
        else root / "current.json"
    )
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
    finally:
        if temp_path.exists():
            temp_path.unlink()
