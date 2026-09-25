"""Godot 引擎安装器测试（tools/install_godot.py）。

不联网：下载函数 _http_get 被 mock 为内存路由（zip/校验和 sidecar 按URL分发），
重点验证：版本钉死、校验和 fail-closed、zip 条目安全、原子发布、幂等、check。
"""

import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from src.core.result import is_contract_valid

_MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "install_godot.py"
_spec = importlib.util.spec_from_file_location("install_godot", _MODULE_PATH)
ig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ig)

VERSION = "4.6.3"
ASSET = f"Godot_v{VERSION}-stable_win64.exe.zip"
FAKE_BIN = "MZfake-godot-binary"


def _build_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"Godot_v{VERSION}-stable_win64.exe", FAKE_BIN)
    return buf.getvalue()


# zipfile 写入带时间戳：模块级生成一次并全测试复用，保证哈希稳定
DEFAULT_ZIP = _build_zip_bytes()
DEFAULT_SHA512 = hashlib.sha512(DEFAULT_ZIP).hexdigest()


def _asset_url() -> str:
    return ig.RELEASE_URL.format(tag=f"{VERSION}-stable", asset=ASSET)


def _patch_http(monkeypatch, *, zip_bytes=None, sidecar_sha512=None, no_sidecar=False):
    """内存路由：asset URL → zip；{asset}.sha512 → sidecar 文本。

    no_sidecar=True 时模拟 sidecar 404（sha512/sha256 都拉不到）。
    """
    calls = []

    def fake_get(url, dest, hasher=None):
        calls.append(url)
        if url == _asset_url():
            data = zip_bytes if zip_bytes is not None else DEFAULT_ZIP
        elif no_sidecar:
            raise OSError(404, "sidecar missing")
        elif url == _asset_url() + ".sha512":
            digest = hashlib.sha512(zip_bytes if zip_bytes is not None else DEFAULT_ZIP).hexdigest()
            data = (sidecar_sha512 if sidecar_sha512 is not None else f"{digest}  {ASSET}").encode()
        else:
            raise OSError(404, f"unexpected: {url}")
        dest.write_bytes(data)
        if hasher is not None:
            hasher.update(data)
        return dest

    monkeypatch.setattr(ig, "_http_get", fake_get)
    return calls


@pytest.fixture()
def dest(tmp_path):
    """安装根目录。

    pytest basetemp（.tmp/pytest）目录可能跨会话残留，而安装器幂等分支
    会读取旧 installed.json 直接返回——先清空保证每个用例从零开始。
    """
    import shutil

    d = tmp_path / "godot_home"
    shutil.rmtree(d, ignore_errors=True)
    return d


class TestVersionPinning:
    def test_rejects_latest(self, dest):
        for bad in ("latest", "*", "stable"):
            r = ig.install(bad, dest=str(dest))
            assert r["ok"] is False
            assert any("钉死" in e for e in r["errors"])
            assert is_contract_valid(r)

    def test_unknown_platform_rejected(self, dest):
        r = ig.install(VERSION, platform="ps5", dest=str(dest))
        assert r["ok"] is False
        assert any("平台" in e for e in r["errors"])


class TestInstallHappyPath:
    def test_installs_with_explicit_sha512(self, dest, monkeypatch):
        calls = _patch_http(monkeypatch)  # sidecar 存在，但显式 sha512 优先生效
        digest = DEFAULT_SHA512
        r = ig.install(VERSION, platform="win64", dest=str(dest), sha512=digest)
        assert r["ok"] is True, r["errors"]
        assert is_contract_valid(r)
        install_dir = dest / f"godot-{VERSION}"
        assert (install_dir / f"Godot_v{VERSION}-stable_win64.exe").read_text() == FAKE_BIN
        manifest = json.loads((install_dir / "installed.json").read_text(encoding="utf-8"))
        assert manifest["version"] == VERSION
        assert manifest["sha512"] == digest
        assert Path(manifest["binary"]).is_file()
        assert (install_dir / "godot_env.sh").is_file()
        assert (install_dir / "godot_env.cmd").is_file()
        # staging 清理干净
        assert not [p for p in dest.iterdir() if p.name.startswith(".staging-")]
        assert len(calls) == 1  # sidecar 未被下载（显式提供）

    def test_sidecar_checksum_used_when_no_explicit(self, dest, monkeypatch):
        _patch_http(monkeypatch)
        r = ig.install(VERSION, platform="win64", dest=str(dest))
        assert r["ok"] is True, r["errors"]
        assert r["payload"]["sha512"] == DEFAULT_SHA512

    def test_idempotent_second_run_skips_download(self, dest, monkeypatch):
        calls = _patch_http(monkeypatch)
        assert ig.install(VERSION, platform="win64", dest=str(dest))["ok"] is True
        assert len(calls) == 2  # 首次：sidecar 校验和 + asset 本体
        r = ig.install(VERSION, platform="win64", dest=str(dest))
        assert r["ok"] is True
        assert any("已安装" in w for w in r["warnings"])
        assert len(calls) == 2  # 第二次幂等跳过，没有新增网络请求


class TestFailClosed:
    def test_checksum_mismatch_blocks_publish(self, dest, monkeypatch):
        _patch_http(monkeypatch, sidecar_sha512="0" * 128 + "  x")
        r = ig.install(VERSION, platform="win64", dest=str(dest))
        assert r["ok"] is False
        assert any("sha512 不匹配" in e for e in r["errors"])
        assert not (dest / f"godot-{VERSION}").exists()

    def test_no_checksum_fails_unless_allowed(self, dest, monkeypatch):
        _patch_http(monkeypatch, no_sidecar=True)  # sidecar 404
        r = ig.install(VERSION, platform="win64", dest=str(dest))
        assert r["ok"] is False
        assert any("拒绝安装" in e for e in r["errors"])
        assert not (dest / f"godot-{VERSION}").exists()

        r2 = ig.install(VERSION, platform="win64", dest=str(dest), allow_unverified=True)
        assert r2["ok"] is True
        assert any("不可信" in w for w in r2["warnings"])

    def test_unsafe_zip_entry_rejected(self, dest, monkeypatch):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"Godot_v{VERSION}-stable_win64.exe", FAKE_BIN)
            zf.writestr("../evil.exe", "pwned")
        _patch_http(monkeypatch, zip_bytes=buf.getvalue())  # zip_bytes 显式给定
        r = ig.install(VERSION, platform="win64", dest=str(dest))
        assert r["ok"] is False
        assert any("不安全条目" in e for e in r["errors"])
        assert not (dest.parent / "evil.exe").exists()


class TestCheck:
    def test_check_after_install(self, dest, monkeypatch):
        _patch_http(monkeypatch)
        assert ig.install(VERSION, platform="win64", dest=str(dest))["ok"] is True
        r = ig.check(VERSION, str(dest))
        assert r["ok"] is True
        assert r["payload"]["version"] == VERSION
        assert is_contract_valid(r)

    def test_check_missing_fails_attributably(self, dest):
        r = ig.check(VERSION, str(dest))
        assert r["ok"] is False
        assert r["errors"]
        assert is_contract_valid(r)


class TestMain:
    def test_json_output_and_exit_code(self, dest, monkeypatch, capsys):
        _patch_http(monkeypatch)
        code = ig.main([
            "--version", VERSION, "--platform", "win64",
            "--dest", str(dest), "--json",
        ])
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True and out["operation"] == "engine.install_godot"

        code_bad = ig.main([
            "--version", "latest", "--platform", "win64",
            "--dest", str(dest), "--json",
        ])
        assert code_bad == 1
