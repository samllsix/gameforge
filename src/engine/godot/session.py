"""GameForge - Godot 引擎对接的唯一入口（M6-06 / M6-07）。

"引擎在哪"此前复制粘贴了 6 遍（``GodotCompiler`` / ``GodotEditor`` /
``GodotSupervisor`` / ``GodotRuntimeSmoke`` / ``PlaytestRunner`` /
``api._resolve_editor_path``），每处同一句
``config.godot.editor_path or GODOT_EDITOR_PATH``，且零自动发现——新机器
必须手填配置（macOS 还要填到 .app 包内）才能用（M6-07）。

本模块把两个答案收口到 ``GodotSession``：

* ``executable(config)`` — 引擎可执行文件：config → env → 常见安装位置自动发现
* ``env(base)``          — 子进程环境变量（``godot_user_env`` 实现迁入）

项目目录寻址另有 ``src.core.paths`` 契约（``projects/<project_id>/``），
不在本模块职责内；``project_path`` 配置的迁移是后续步骤，届时同样收口。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import structlog

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ENV_TMPL = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _resolve_env(value: str) -> str:
    """展开 ``${VAR}`` / ``${VAR:default}`` 形式的环境变量模板。

    配置加载器（yaml.safe_load）不会解析这类模板，而 config.yaml 中的
    ``godot.editor_path: ${GODOT_EDITOR_PATH:}`` 会原样保留为字面量字符串。
    若不展开，``GodotEditor`` 会拿到字面量 ``${GODOT_EDITOR_PATH:}``（truthy），
    导致 ``or os.getenv(...)`` 兜底永不触发、``validate()`` 误判引擎缺失。
    """
    if not value or "${" not in value:
        return value

    def _sub(m: "re.Match") -> str:
        name = m.group(1)
        default = m.group(2) if m.group(2) is not None else ""
        return os.getenv(name, default)

    return _ENV_TMPL.sub(_sub, value)


def _normalize_godot_path(p: str) -> str:
    """把 Git Bash 风格的 /d/godot/... 归一化为 Windows 的 D:/godot/...

    在 Windows 上 Python 的 os.path 不会翻译 /d/ 前缀，直接当成相对路径，
    导致 isfile 失败、headless 路径无法识别。
    """
    if not p:
        return p
    if (
        p.startswith("/")
        and len(p) > 2
        and p[1].isalpha()
        and p[2] == "/"
    ):
        return p[1].upper() + ":" + p[2:]
    return p


# ── Godot user:// 数据目录 ──────────────────────────────────────────────────
# Godot 启动时必须能写 user:// 数据目录，否则不是优雅退出而是**段错误崩溃**：
#   ERROR: Error attempting to create data dir: .../Godot/app_userdata/<项目名>
#   handle_crash: Program crashed with signal 11 (RotatedFileLogger 拿不到 user://logs)
# 受限环境（容器 / CI / 只读 HOME / 文件沙箱）里该位置常常不可写，headless
# 校验会因此全部误判为"通过"（崩在写日志阶段，根本没校验到脚本）。
#
# 注意不能无条件重定向 HOME：Godot 的导出模板装在
#   ~/Library/Application Support/Godot/export_templates/
# 改了 HOME 会让 Web/Windows 导出找不到模板。所以这里先探测真实位置是否可写，
# 只在不可写时才回退到工作区内的 data/godot_home。
_godot_home_logged = False


def _godot_default_user_root() -> Optional[str]:
    """Godot 存放 user:// 数据的平台默认根目录。"""
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    if sys.platform == "win32":
        return os.environ.get("APPDATA")
    return os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )


def _godot_user_root_writable() -> bool:
    """真实 user:// 数据根是否可写（探测失败即视为不可写）。

    只做 ``makedirs(exist_ok=True)`` 是不够的：目录已存在但不可写时它静默返回，
    会把不可写误判成可写（macOS 文件沙箱下正是这种情况，随后 Godot 段错误）。
    必须真的落一个探针文件。
    """
    root = _godot_default_user_root()
    if not root:
        return False
    target = os.path.join(root, "Godot")
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, ".gameforge_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def godot_user_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """构造运行 Godot 子进程用的环境变量（实现见 ``GodotSession.env``）。

    Args:
        base: 基础环境；缺省用 ``os.environ``（不修改父进程环境）

    Returns:
        可直接传给 ``subprocess.run(..., env=...)`` 的字典。真实 user:// 位置
        可写时原样返回；不可写时把 HOME / XDG_DATA_HOME / APPDATA 指向
        ``data/godot_home``（可由 GAMEFORGE_DATA_ROOT 整体迁移）。
    """
    return GodotSession.env(base)


# ── 引擎自动发现（M6-07） ───────────────────────────────────────────────────

def _versioned_dir_candidates(base: Path) -> Iterator[str]:
    """版本化安装目录内的可执行文件：``<base>/Godot_v4.x*/Godot*.exe``。"""
    if not base.is_dir():
        return
    for sub in sorted(base.glob("*")):
        if not sub.is_dir():
            continue
        for child in sorted(sub.glob("*")):
            if (
                child.is_file()
                and child.name.lower().startswith("godot")
                and child.suffix.lower() in ("", ".exe")
            ):
                yield str(child)


def _iter_candidate_paths() -> Iterator[str]:
    """常见安装位置的候选可执行文件（按平台，确定性顺序）。"""
    if sys.platform == "darwin":
        yield "/Applications/Godot.app/Contents/MacOS/Godot"
        yield str(
            Path.home() / "Applications" / "Godot.app" / "Contents" / "MacOS" / "Godot"
        )
    elif sys.platform == "win32":
        bases = [
            os.environ[v]
            for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
            if os.environ.get(v)
        ]
        bases += [r"D:\Godot", r"D:\godot", r"C:\Godot"]
        for base in bases:
            yield str(Path(base) / "Godot" / "Godot.exe")
            yield str(Path(base) / "Godot.exe")
        for base in (Path(r"D:\Godot"), Path(r"D:\godot")):
            yield from _versioned_dir_candidates(base)
    else:  # linux / 其他类 Unix
        for cand in (
            "/usr/local/bin/godot",
            "/usr/bin/godot",
            "/opt/godot/godot",
            "/snap/bin/godot",
        ):
            yield cand

    # 仓库内置 tools/godot/（版本化随仓库分发，解压即用；含版本化子目录形态）
    yield from _versioned_dir_candidates(_REPO_ROOT / "tools" / "godot")
    repo = _REPO_ROOT / "tools" / "godot"
    if repo.is_dir():
        for child in sorted(repo.glob("*")):
            if child.is_file() and child.name.lower().startswith("godot"):
                yield str(child)


def _discover_executable() -> str:
    """常见安装位置自动发现；找不到返回 ""。

    设置 ``GAMEFORGE_NO_GODOT_AUTODISCOVER=1`` 可整体关闭（测试宿主免疫：
    开发机装了 Godot，自动发现会让"未配置引擎"的用例拿到真实引擎而改变分支）。
    """
    if os.environ.get("GAMEFORGE_NO_GODOT_AUTODISCOVER", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        return ""
    for cand in _iter_candidate_paths():
        if cand and os.path.isfile(cand):
            return cand
    return ""


class GodotSession:
    """Godot 引擎对接的唯一入口。"""

    @staticmethod
    def executable(config: Optional[Dict[str, Any]] = None) -> str:
        """引擎可执行文件路径。

        优先级：``config.godot.editor_path`` → ``GODOT_EDITOR_PATH`` →
        常见安装位置自动发现（macOS ``/Applications/Godot.app``、Linux
        ``/usr/local/bin/godot``、Windows 常见目录 + 仓库 ``tools/godot/``）。
        都找不到返回 ""（调用方按"引擎不可用"处理）。
        """
        cfg = (config or {}).get("godot", {}) or {}
        raw = _resolve_env(
            cfg.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        )
        if raw:
            return _normalize_godot_path(raw)
        found = _discover_executable()
        if found:
            logger.info("godot.executable_auto_discovered", path=found)
            return _normalize_godot_path(found)
        return ""

    @staticmethod
    def env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构造运行 Godot 子进程用的环境变量。

        Args:
            base: 基础环境；缺省用 ``os.environ``（不修改父进程环境）

        Returns:
            可直接传给 ``subprocess.run(..., env=...)`` 的字典。真实 user://
            位置可写时原样返回；不可写时把 HOME / XDG_DATA_HOME / APPDATA
            指向 ``data/godot_home``（可由 GAMEFORGE_DATA_ROOT 整体迁移）。
        """
        global _godot_home_logged

        env = dict(os.environ if base is None else base)
        if _godot_user_root_writable():
            return env

        from src.core import paths

        fallback = paths.DATA_ROOT / "godot_home"
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError as e:  # 连工作区都写不了：交给 Godot 自己报错，别在这里吞掉
            logger.warning("godot.user_home_fallback_failed", error=str(e))
            return env

        env["HOME"] = str(fallback)
        env["XDG_DATA_HOME"] = str(fallback / "share")
        env["APPDATA"] = str(fallback / "AppData" / "Roaming")
        try:
            os.makedirs(env["XDG_DATA_HOME"], exist_ok=True)
            os.makedirs(env["APPDATA"], exist_ok=True)
        except OSError:
            pass

        if not _godot_home_logged:
            _godot_home_logged = True
            logger.warning(
                "godot.user_home_redirected",
                reason="默认 user:// 数据目录不可写，Godot 会段错误崩溃",
                default=_godot_default_user_root(),
                fallback=str(fallback),
            )
        return env
