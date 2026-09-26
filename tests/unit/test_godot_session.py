"""GodotSession 引擎对接收口测试（M6-06 / M6-07）。

覆盖：
- executable 优先级：config.godot.editor_path > GODOT_EDITOR_PATH > 自动发现
- 自动发现：三平台常见安装位置 + 仓库 tools/godot/ 版本化子目录
- ${VAR} 模板展开与 /d/ 路径归一化（既有行为不回归）
- env：godot_user_env 迁入 GodotSession.env 后行为不变
- 6 处历史解析点全部委托 GodotSession（改一处即全部生效）
"""

import os
import sys
from pathlib import Path

import pytest

from src.engine.godot.session import (
    GodotSession,
    _discover_executable,
    _iter_candidate_paths,
    _normalize_godot_path,
    _resolve_env,
    godot_user_env,
)


@pytest.fixture()
def discovery_on(monkeypatch):
    """打开自动发现（conftest 默认关闭以保宿主免疫）。"""
    monkeypatch.delenv("GAMEFORGE_NO_GODOT_AUTODISCOVER", raising=False)


# ── executable：优先级 ──────────────────────────────────────────────────────


def test_executable_prefers_config(monkeypatch):
    monkeypatch.setenv("GODOT_EDITOR_PATH", "C:/env/godot.exe")
    cfg = {"godot": {"editor_path": "C:/cfg/godot.exe"}}
    assert GodotSession.executable(cfg) == "C:/cfg/godot.exe"


def test_executable_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GODOT_EDITOR_PATH", "C:/env/godot.exe")
    assert GodotSession.executable({"godot": {}}) == "C:/env/godot.exe"


def test_executable_env_template_expanded(monkeypatch):
    """config.yaml 的 ${GODOT_EDITOR_PATH:} 模板必须展开，不能被当字面量。"""
    monkeypatch.setenv("GODOT_EDITOR_PATH", "C:/env/godot.exe")
    cfg = {"godot": {"editor_path": "${GODOT_EDITOR_PATH:}"}}
    assert GodotSession.executable(cfg) == "C:/env/godot.exe"


def test_executable_normalizes_git_bash_path():
    assert GodotSession.executable(
        {"godot": {"editor_path": "/d/godot/Godot.exe"}}
    ) == "D:/godot/Godot.exe"


def test_executable_empty_when_nothing_found(monkeypatch):
    monkeypatch.delenv("GODOT_EDITOR_PATH", raising=False)
    assert GodotSession.executable({"godot": {}}) == ""


def test_executable_discovery_kill_switch(monkeypatch, tmp_path):
    """GAMEFORGE_NO_GODOT_AUTODISCOVER=1 时即使有候选也不发现。"""
    monkeypatch.setenv("GAMEFORGE_NO_GODOT_AUTODISCOVER", "1")
    monkeypatch.setattr(
        "src.engine.godot.session._iter_candidate_paths",
        lambda: iter([str(tmp_path / "godot")]),
    )
    (tmp_path / "godot").write_text("", encoding="utf-8")
    monkeypatch.delenv("GODOT_EDITOR_PATH", raising=False)
    assert GodotSession.executable({"godot": {}}) == ""


# ── 自动发现 ────────────────────────────────────────────────────────────────


def test_discovery_finds_first_existing_candidate(discovery_on, monkeypatch, tmp_path):
    """候选按顺序取第一个真实存在的文件。"""
    a, b = tmp_path / "a", tmp_path / "b"
    b.write_text("", encoding="utf-8")  # 只有 b 存在
    monkeypatch.setattr(
        "src.engine.godot.session._iter_candidate_paths",
        lambda: iter([str(a), str(b)]),
    )
    assert _discover_executable() == str(b)


def test_discovery_returns_empty_when_no_candidate_exists(discovery_on, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.engine.godot.session._iter_candidate_paths",
        lambda: iter([str(tmp_path / "nope")]),
    )
    assert _discover_executable() == ""


def test_discovery_candidates_cover_platform_common_paths(monkeypatch):
    """三平台常见安装位置都要出现在候选里（M6-07 配置门槛）。"""
    monkeypatch.setattr(sys, "platform", "darwin")
    darwin = list(_iter_candidate_paths())
    assert any("Godot.app/Contents/MacOS/Godot" in c for c in darwin)

    monkeypatch.setattr(sys, "platform", "linux")
    linux = list(_iter_candidate_paths())
    assert "/usr/local/bin/godot" in linux

    monkeypatch.setattr(sys, "platform", "win32")
    win = list(_iter_candidate_paths())
    assert any(c.endswith("Godot.exe") for c in win)


def test_discovery_includes_repo_tools_godot(monkeypatch, tmp_path):
    """仓库内置 tools/godot/ 版本化子目录（解压即用）要被发现。"""
    from src.engine.godot import session

    monkeypatch.setattr(session, "_REPO_ROOT", tmp_path)
    versioned = tmp_path / "tools" / "godot" / "Godot_v4.6.3-stable_win64.exe"
    versioned.mkdir(parents=True)
    exe = versioned / "Godot_v4.6.3-stable_win64.exe"
    exe.write_text("", encoding="utf-8")

    cands = list(_iter_candidate_paths())
    assert str(exe) in cands


# ── env：godot_user_env 迁入后行为不变 ──────────────────────────────────────


def test_env_passthrough_when_user_root_writable(monkeypatch):
    """真实 user:// 根可写时原样返回 base。"""
    monkeypatch.setattr(
        "src.engine.godot.session._godot_user_root_writable", lambda: True
    )
    base = {"FOO": "bar"}
    assert godot_user_env(base) == {"FOO": "bar"}
    assert GodotSession.env(base) == {"FOO": "bar"}


def test_env_redirects_home_when_user_root_unwritable(monkeypatch, tmp_path):
    """不可写时 HOME/XDG_DATA_HOME/APPDATA 指向 data/godot_home。"""
    from src.core import paths

    monkeypatch.setattr(
        "src.engine.godot.session._godot_user_root_writable", lambda: False
    )
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path / "data")

    env = GodotSession.env({"FOO": "bar"})
    fallback = tmp_path / "data" / "godot_home"
    assert env["HOME"] == str(fallback)
    assert env["XDG_DATA_HOME"] == str(fallback / "share")
    assert env["APPDATA"] == str(fallback / "AppData" / "Roaming")
    assert env["FOO"] == "bar"  # base 其余变量保留


# ── 辅助函数不回归 ──────────────────────────────────────────────────────────


def test_resolve_env_template():
    monkey = os.environ
    monkey.setdefault("GF_TEST_VAR", "x")
    assert _resolve_env("${GF_TEST_VAR}") == "x"
    assert _resolve_env("${GF_TEST_MISSING:def}") == "def"
    assert _resolve_env("plain") == "plain"


def test_normalize_path():
    assert _normalize_godot_path("/d/godot/x.exe") == "D:/godot/x.exe"
    assert _normalize_godot_path("C:/godot/x.exe") == "C:/godot/x.exe"
    assert _normalize_godot_path("") == ""


# ── 6 处历史解析点委托 GodotSession ─────────────────────────────────────────


class TestCallSitesDelegate:
    """M6-06：改 GodotSession 一处，6 个消费方全部生效。"""

    def test_godot_editor_uses_session(self, monkeypatch, tmp_path):
        from src.engine.godot import GodotEditor

        monkeypatch.setenv("GODOT_EDITOR_PATH", str(tmp_path / "env_godot.exe"))
        editor = GodotEditor({"godot": {}})
        assert editor.editor_path == str(tmp_path / "env_godot.exe")

    def test_runtime_smoke_uses_session(self, monkeypatch, tmp_path):
        from src.engine.godot.runtime_smoke import GodotRuntimeSmoke

        monkeypatch.setenv("GODOT_EDITOR_PATH", str(tmp_path / "env_godot.exe"))
        smoke = GodotRuntimeSmoke({"godot": {}})
        assert smoke.editor_path == str(tmp_path / "env_godot.exe")

    def test_playtest_uses_session(self, monkeypatch, tmp_path):
        from src.engine.godot.playtest import PlaytestRunner

        monkeypatch.setenv("GODOT_EDITOR_PATH", str(tmp_path / "env_godot.exe"))
        runner = PlaytestRunner({"godot": {}})
        assert runner.editor_path == str(tmp_path / "env_godot.exe")

    def test_supervisor_uses_session(self, monkeypatch, tmp_path):
        from src.engine.godot.supervisor import GodotSupervisor

        monkeypatch.setenv("GODOT_EDITOR_PATH", str(tmp_path / "env_godot.exe"))
        sup = GodotSupervisor({"preview": {}})
        assert sup.editor_path == str(tmp_path / "env_godot.exe")

    def test_api_resolve_editor_path_uses_session(self, monkeypatch, tmp_path):
        import src.api.main as main_mod

        monkeypatch.setenv("GODOT_EDITOR_PATH", str(tmp_path / "env_godot.exe"))
        assert main_mod._resolve_editor_path() == str(tmp_path / "env_godot.exe")

    def test_config_wins_over_env_at_all_sites(self, monkeypatch, tmp_path):
        """config 显式配置优先于 env（含编译器内部构造的 compiler）。"""
        from src.engine.godot import GodotEditor

        monkeypatch.setenv("GODOT_EDITOR_PATH", str(tmp_path / "env_godot.exe"))
        cfg_path = str(tmp_path / "cfg_godot.exe")
        editor = GodotEditor({"godot": {"editor_path": cfg_path}})
        assert editor.editor_path == cfg_path
        assert editor.compiler.editor_path == cfg_path
