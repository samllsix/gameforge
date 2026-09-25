"""GameForge - Godot 引擎安装器（钉版本 + 校验和 + 原子发布）。

借鉴 GameFactory-3A 的引擎安装约束：Agent / CI 拿到的引擎工具链必须
**版本确定、校验和可验证、发布原子化**——杜绝"每台机器随手下载一个
Godot，行为不可复现"的环境漂移。

设计（与 OpenDCAI/GameFactory-3A 的 engine_install 对齐）：
- 版本钉死：``--version latest`` 直接拒绝；只从官方 GitHub release 下载；
- 校验和 fail-closed：显式 ``--sha512`` > 官方 sidecar（``.sha512``/``.sha256``）
  > 都没有则失败，除非显式 ``--allow-unverified``；
- zip 条目安全：拒绝绝对路径 / ``..`` 穿越；
- 原子发布：全部先落在 ``<dest>/.staging-*``，校验通过后整目录 rename，
  失败自动清理，主线永远看不到半成品；
- 幂等：``godot-<版本>/installed.json`` 记录（版本、sha512、二进制路径），
  重复安装直接 ok 跳过；``--check`` 只校验不下载；
- 产物：env shim（``godot_env.sh`` / ``godot_env.cmd``）写出
  ``GODOT_EDITOR_PATH``，source 后即可被工作流读取。

输出遵循 src/core/result 契约（``gameforge.operation_result.v1``）。

用法::

    python tools/install_godot.py --version 4.6.3 --json
    python tools/install_godot.py --version 4.6.3 --check --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.result import add_artifact, new_operation_result  # noqa: E402

RELEASE_URL = "https://github.com/godotengine/godot/releases/download/{tag}/{asset}"
MANIFEST_NAME = "installed.json"
ENV_SHIM_NAMES = ("godot_env.sh", "godot_env.cmd")
STAGING_PREFIX = ".staging-"


def _default_platform() -> str:
    if sys.platform == "win32":
        return "win64"
    if sys.platform == "darwin":
        return "macos.universal"
    return "linux.x86_64"


def _asset_name(version: str, platform: str) -> str:
    if platform == "win64":
        return f"Godot_v{version}-stable_win64.exe.zip"
    if platform.startswith("linux"):
        arch = platform.split(".", 1)[1] if "." in platform else "x86_64"
        return f"Godot_v{version}-stable_linux.{arch}.zip"
    if platform.startswith("macos"):
        return f"Godot_v{version}-stable_macos.universal.zip"
    raise ValueError(f"不支持的平台: {platform}")


def _binary_suffix(platform: str) -> str:
    """安装目录里引擎二进制的相对查找规则（平台原生可执行名）。"""
    if platform == "win64":
        return ".exe"
    return ""


def _is_safe_zip_entry(name: str) -> bool:
    from pathlib import PurePosixPath

    p = PurePosixPath(name)
    if p.is_absolute() or name.startswith("\\"):
        return False
    return ".." not in p.parts


def _http_get(url: str, dest: Path, hasher: Optional["hashlib._Hash"] = None) -> Path:
    """流式下载 url 到 dest（边下边算哈希），返回 dest。"""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "gameforge-installer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:  # noqa: S310
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            if hasher is not None:
                hasher.update(chunk)
    return dest


def _fetch_checksum_sidecar(asset_url: str) -> tuple:
    """尝试取官方 sidecar 校验和，返回 (algo, hex) 或 (None, reason)。"""
    for algo, suffix in (("sha512", ".sha512"), ("sha256", ".sha256")):
        try:
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                sidecar = _http_get(asset_url + suffix, Path(td) / "sidecar")
                text = sidecar.read_text(encoding="utf-8", errors="replace")
            # sidecar 格式：<hex>  <文件名>（校验和行取第一个 token）
            token = text.strip().split()[0].lower()
            if len(token) >= 64:
                return algo, token
        except Exception:  # noqa: BLE001,S110
            continue
    return None, "sidecar_unavailable"


def _locate_binary(extract_dir: Path, version: str, platform: str) -> Optional[Path]:
    """在解压目录里定位引擎可执行文件。"""
    suffix = _binary_suffix(platform)
    if platform == "win64":
        exact = extract_dir / f"Godot_v{version}-stable_win64.exe"
        if exact.is_file():
            return exact
    if platform.startswith("macos"):
        for p in (extract_dir / "Godot.app" / "Contents" / "MacOS").glob("Godot*"):
            if p.is_file():
                return p
    # 兜底：按名字模糊匹配
    candidates = [
        p for p in extract_dir.rglob("Godot*")
        if p.is_file() and (p.suffix == suffix) and ".import" not in p.name
    ]
    return candidates[0] if candidates else None


def _write_env_shims(install_dir: Path, binary: Path) -> None:
    """写 env shim：source/调用后 GODOT_EDITOR_PATH 指向本安装的引擎。"""
    sh = install_dir / "godot_env.sh"
    sh.write_text(
        f'export GODOT_EDITOR_PATH="{binary.as_posix()}"\n',
        encoding="utf-8",
    )
    cmd = install_dir / "godot_env.cmd"
    cmd.write_text(
        f'@echo off\r\nset "GODOT_EDITOR_PATH={binary}"\r\n',
        encoding="utf-8",
    )


def install(
    version: str,
    *,
    platform: str = "auto",
    dest: Optional[str] = None,
    sha512: Optional[str] = None,
    allow_unverified: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """安装指定版本 Godot 到 dest，返回 src/core/result 契约结果。"""
    result = new_operation_result("engine.install_godot")

    if not version or version.lower() in {"latest", "*", "stable"}:
        result["errors"].append(f"版本必须钉死，拒绝 {version!r}（如 --version 4.6.3）")
        return result
    tag = version if version.endswith("-stable") else f"{version}-stable"
    if platform == "auto":
        platform = _default_platform()
    try:
        asset = _asset_name(version, platform)
    except ValueError as e:
        result["errors"].append(str(e))
        return result
    asset_url = RELEASE_URL.format(tag=tag, asset=asset)

    dest_dir = Path(dest) if dest else Path(
        os.environ.get("GAMEFORGE_GODOT_HOME") or (_REPO_ROOT / "tools" / "godot")
    )
    install_dir = dest_dir / f"godot-{version}"
    manifest_path = install_dir / MANIFEST_NAME

    # 幂等：已安装且校验通过 → 直接 ok
    if manifest_path.is_file() and not force:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        if manifest and manifest.get("version") == version and Path(manifest.get("binary", "")).is_file():
            result["ok"] = True
            result["warnings"].append(f"已安装，跳过（{install_dir}）；--force 可重装")
            result["payload"] = manifest
            add_artifact(result, "manifest", manifest_path)
            return result

    # 1) 确定期望校验和（fail-closed）
    expected: Dict[str, str] = {}
    if sha512:
        if sha512.startswith("@"):
            expected["sha512"] = Path(sha512[1:]).read_text(encoding="utf-8").strip().split()[0].lower()
        else:
            expected["sha512"] = sha512.strip().lower()
    else:
        algo, token = _fetch_checksum_sidecar(asset_url)
        if algo:
            expected[algo] = token
        elif not allow_unverified:
            result["errors"].append(
                "无显式 --sha512 且官方 sidecar 不可用；为防供应链篡改拒绝安装"
                "（确需跳过请显式 --allow-unverified）"
            )
            return result
        else:
            result["warnings"].append("未验证校验和（--allow-unverified），安装来源不可信")

    # 2) 原子 staging：下载 → 校验 → 解压 → 定位二进制
    staging = dest_dir / f"{STAGING_PREFIX}{version}-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        archive = staging / asset
        hasher = hashlib.sha512()
        _http_get(asset_url, archive, hasher)
        actual = hasher.hexdigest()
        if "sha512" in expected and actual != expected["sha512"]:
            result["errors"].append(
                f"sha512 不匹配：期望 {expected['sha512'][:16]}…，实际 {actual[:16]}…"
            )
            return result
        if "sha256" in expected:
            h256 = hashlib.sha256()
            h256.update(archive.read_bytes())
            if h256.hexdigest() != expected["sha256"]:
                result["errors"].append("sha256 不匹配（官方 sidecar）")
                return result

        extract_dir = staging / "extract"
        with zipfile.ZipFile(archive) as zf:
            bad = [n for n in zf.namelist() if not _is_safe_zip_entry(n)]
            if bad:
                result["errors"].append(f"zip 含不安全条目，拒绝解压: {bad[:3]}")
                return result
            zf.extractall(extract_dir)  # noqa: S202 - 条目已逐个校验

        binary = _locate_binary(extract_dir, version, platform)
        if not binary:
            result["errors"].append("解压后未找到 Godot 可执行文件")
            return result
        if platform != "win64":
            binary.chmod(binary.stat().st_mode | 0o111)

        # 3) 原子发布：staging 里的 extract 内容整目录搬到正式路径
        if install_dir.exists():
            shutil.rmtree(install_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extract_dir), str(install_dir))

        manifest = {
            "version": version,
            "tag": tag,
            "platform": platform,
            "sha512": actual,
            "binary": str((install_dir / binary.relative_to(extract_dir)).resolve()),
            "source_url": asset_url,
            "installed_at": datetime.now().isoformat(),
        }
        (install_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_env_shims(install_dir, Path(manifest["binary"]))
        result["ok"] = True
        result["payload"] = manifest
        add_artifact(result, "binary", manifest["binary"])
        add_artifact(result, "manifest", manifest_path)
        for shim in ENV_SHIM_NAMES:
            add_artifact(result, shim.replace(".", "_"), install_dir / shim)
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def check(version: str, dest: Optional[str] = None, *, reverify: bool = False) -> Dict[str, Any]:
    """校验已安装的引擎（不下载）。"""
    result = new_operation_result("engine.check_godot")
    dest_dir = Path(dest) if dest else Path(
        os.environ.get("GAMEFORGE_GODOT_HOME") or (_REPO_ROOT / "tools" / "godot")
    )
    manifest_path = dest_dir / f"godot-{version}" / MANIFEST_NAME
    if not manifest_path.is_file():
        result["errors"].append(f"未找到安装清单: {manifest_path}")
        return result
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    binary = Path(manifest.get("binary", ""))
    if not binary.is_file():
        result["errors"].append(f"引擎二进制缺失: {binary}")
        return result
    if reverify and manifest.get("sha512"):
        h = hashlib.sha512()
        with open(binary, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != manifest["sha512"]:
            result["errors"].append("二进制 sha512 与安装清单不一致（可能被替换）")
            return result
    result["ok"] = True
    result["payload"] = manifest
    add_artifact(result, "manifest", manifest_path)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GameForge Godot 引擎安装器")
    parser.add_argument("--version", required=True, help="钉死的引擎版本，如 4.6.3（拒绝 latest）")
    parser.add_argument("--platform", default="auto",
                        help="win64 / linux.x86_64 / macos.universal（默认按本机）")
    parser.add_argument("--dest", default=None, help="安装根目录（默认 GAMEFORGE_GODOT_HOME 或 tools/godot）")
    parser.add_argument("--sha512", default=None, help="期望 sha512（hex 或 @文件）；缺省取官方 sidecar")
    parser.add_argument("--allow-unverified", action="store_true", help="无校验和时也允许安装（不推荐）")
    parser.add_argument("--force", action="store_true", help="忽略幂等检查强制重装")
    parser.add_argument("--check", action="store_true", help="只校验已安装引擎，不下载")
    parser.add_argument("--reverify", action="store_true", help="check 时重算二进制哈希")
    parser.add_argument("--json", action="store_true", help="以 JSON（结果契约）输出")
    args = parser.parse_args(argv)

    if args.check:
        result = check(args.version, args.dest, reverify=args.reverify)
    else:
        result = install(
            args.version,
            platform=args.platform,
            dest=args.dest,
            sha512=args.sha512,
            allow_unverified=args.allow_unverified,
            force=args.force,
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        state = "OK" if result["ok"] else "FAILED"
        print(f"[{state}] {result['operation']} v{args.version}")
        for line in result["errors"]:
            print(f"  error: {line}")
        for line in result["warnings"]:
            print(f"  warn:  {line}")
        for name, path in result["artifacts"].items():
            print(f"  artifact[{name}]: {path}")
        if result["payload"] and result["ok"]:
            print(f"  GODOT_EDITOR_PATH={result['payload'].get('binary')}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
