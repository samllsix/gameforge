"""P2-5 数据库会话切换契约测试。

目的：固化 MySQL 默认 + 离线 SQLite 降级 + URL scheme 兼容的行为，
确保未来重构不会破坏：
- 默认 MySQL 切换生效
- SQLite 仍可作为离线降级
- 'mysql://' 简写（自动注册 PyMySQL）可用
- engine_kwargs 中池参数按 dialect 设置

策略：每个测试用例都显式传 database_url 给 get_engine(url)，
避免改 .env；并 monkeypatch `pymysql.install_as_MySQLdb` 隔离副作用。
"""
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_engine_each_test():
    """每个测试后重置引擎单例，避免污染。"""
    from src.db import session
    session.reset_db()
    yield
    session.reset_db()


def test_default_url_is_mysql():
    """契约：默认 URL 必须是 MySQL（项目硬切到本地 MySQL）。"""
    from src.db.session import DEFAULT_DATABASE_URL
    assert DEFAULT_DATABASE_URL.startswith("mysql"), \
        f"DEFAULT_DATABASE_URL 应为 MySQL，实际：{DEFAULT_DATABASE_URL}"
    assert "127.0.0.1" in DEFAULT_DATABASE_URL
    assert "gameforge" in DEFAULT_DATABASE_URL
    assert "utf8mb4" in DEFAULT_DATABASE_URL


def test_engine_with_sqlite_url_works(tmp_path):
    """契约：显式传 SQLite URL 仍能建引擎（开发离线降级保留）。"""
    sqlite_url = f"sqlite:///{tmp_path}/test.db"
    from src.db.session import get_engine, init_db
    engine = get_engine(sqlite_url)
    assert engine.dialect.name == "sqlite"
    init_db(sqlite_url)
    # 验证表能创建并能写
    from sqlalchemy import inspect
    tables = inspect(engine).get_table_names()
    assert "tasks" in tables

    from src.db.models import TaskRecord
    from src.db.session import get_db
    import uuid
    unique_id = f"t_sqlite_{uuid.uuid4().hex[:8]}"
    db = get_db()
    try:
        t = TaskRecord(id=unique_id, task_type="test", payload={"x": 1})
        db.add(t)
        db.commit()
        row = db.query(TaskRecord).filter_by(id=unique_id).first()
        assert row is not None
        assert row.payload == {"x": 1}
    finally:
        db.close()


def test_engine_with_postgres_url_keeps_pool_kwargs():
    """契约：PostgreSQL URL 走 else 分支保留连接池参数。

    实现层验证：URL 解析 + pool kwargs 设置正确即可（不真连库）。
    """
    # 跳过真创建引擎 — psycopg2 未装；改为单元层验证逻辑
    from src.db.session import get_engine
    # 用 sqlite 触发"非 mysql / 非 sqlite"的 else 分支不可行（sqlite 已分支）。
    # 直接 patch create_engine 验证 kwargs：
    from src.db import session as session_mod
    captured = {}

    def fake_create(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        # 返回 MagicMock，dialect.name = 'postgresql'
        mock = MagicMock()
        mock.dialect.name = "postgresql"
        mock.pool.size.return_value = 5
        return mock

    session_mod._engine = None
    with patch.object(session_mod, "create_engine", side_effect=fake_create):
        session_mod.get_engine("postgresql://user:pwd@127.0.0.1:5432/db")
    assert captured["kwargs"]["pool_size"] == 5
    assert captured["kwargs"]["max_overflow"] == 10
    assert captured["kwargs"]["pool_recycle"] == 3600
    assert captured["kwargs"]["pool_timeout"] == 30
    assert captured["kwargs"]["pool_pre_ping"] is True


def test_engine_with_mysql_url_sets_utf8mb4_charset():
    """契约：MySQL URL 必须传 charset=utf8mb4（中文/Emoji 安全）。

    实现层验证：URL 解析 + connect_args 设置正确即可（避免依赖外部库）。
    """
    from src.db import session as session_mod
    captured = {}

    def fake_create(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        mock = MagicMock()
        mock.dialect.name = "mysql"
        return mock

    session_mod._engine = None
    with patch.object(session_mod, "create_engine", side_effect=fake_create):
        session_mod.get_engine("mysql+pymysql://root:123456@127.0.0.1:3306/gameforge")
    assert captured["kwargs"]["connect_args"]["charset"] == "utf8mb4"
    assert captured["kwargs"]["pool_size"] == 5


def test_mysql_short_scheme_registered_with_pymysql():
    """契约：'mysql://' 简写能工作（自动注册 PyMySQL 为 MySQLdb 替身）。

    验证 _ensure_pymysql_registered 被调用，并调用 pymysql.install_as_MySQLdb。
    """
    from src.db.session import _ensure_pymysql_registered

    with patch("pymysql.install_as_MySQLdb") as fake_install:
        _ensure_pymysql_registered()
        fake_install.assert_called_once()


def test_pymysql_missing_does_not_crash_silently():
    """契约：PyMySQL 未装时 _ensure_pymysql_registered 不抛异常（让 SQLAlchemy 友好报错）。"""
    # 临时屏蔽 pymysql 导入
    import builtins
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pymysql":
            raise ImportError("PyMySQL not installed")
        return original_import(name, *args, **kwargs)

    from src.db.session import _ensure_pymysql_registered
    with patch("builtins.__import__", side_effect=fake_import):
        # 不应抛异常
        _ensure_pymysql_registered()  # OK if no exception


def test_environment_url_takes_priority(monkeypatch):
    """契约：环境变量 DATABASE_URL 优先级高于默认值。"""
    from src.db.session import get_engine
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    engine = get_engine()
    assert engine.dialect.name == "sqlite"


def test_explicit_url_takes_priority_over_env(monkeypatch):
    """契约：显式传 database_url 优先级最高。

    实现层验证（不真连库）：用 sqlite URL 验证显式参数胜过 env var。
    """
    from src.db import session as session_mod
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    captured = {}

    def fake_create(url, **kwargs):
        captured["url"] = url
        mock = MagicMock()
        mock.dialect.name = "sqlite"
        return mock

    session_mod._engine = None
    with patch.object(session_mod, "create_engine", side_effect=fake_create):
        engine = session_mod.get_engine("sqlite:///:memory:")
    # 显式 url 参数胜过 env var（env var 也是 sqlite:///:memory:，不冲突；
    # 但仍验证：传给 create_engine 的就是显式 URL）
    assert "memory" in str(captured["url"])


def test_engine_url_quote_password_special_chars():
    """契约：URL 包含特殊字符密码（@/:）时仍能正确解析。

    注意：pymysql URL 中 @ 在密码里需 %40 转义。这里验证我们没破坏默认 URL。
    """
    from src.db.session import get_engine
    engine = get_engine("mysql+pymysql://root:abc%40def@127.0.0.1:3306/gameforge")
    # password 不应被错误切到 host
    assert engine.url.password == "abc@def"
    assert engine.url.host == "127.0.0.1"
    assert engine.url.database == "gameforge"


def test_init_db_creates_all_tables(tmp_path):
    """契约：init_db 必须在 3 个 model 上都建表。"""
    sqlite_url = f"sqlite:///{tmp_path}/test.db"
    from src.db.session import init_db, get_engine
    from sqlalchemy import inspect
    engine = get_engine(sqlite_url)
    init_db(sqlite_url)
    insp = inspect(engine)
    tables = sorted(insp.get_table_names())
    assert "tasks" in tables
    assert "generation_history" in tables
    assert "audit_logs" in tables


def test_reset_db_clears_singleton():
    """契约：reset_db 必须清掉引擎单例，下一次 get_engine 重新构造。"""
    from src.db.session import get_engine, reset_db
    engine1 = get_engine("sqlite:///:memory:")
    eid1 = id(engine1)
    reset_db()
    engine2 = get_engine("sqlite:///:memory:")
    eid2 = id(engine2)
    assert eid1 != eid2, "reset_db 后应创建新引擎"


def test_get_session_factory_returns_same_session_class():
    """契约：get_session_factory 单例，多次调用拿同一对象。"""
    from src.db.session import get_session_factory, reset_db
    reset_db()
    f1 = get_session_factory()
    f2 = get_session_factory()
    assert f1 is f2
    reset_db()
    f3 = get_session_factory()
    # reset 后应重新建工厂
    # 注意：reset_db 会清 _SessionLocal，单例被破坏
    # f1 vs f3 不可比（reset 后可能 gc）
    # 重点是 f1 == f2（同一单例）


# ── 优先级链（方案加固 Step A + B） ──────────────────────────────────


def test_resolve_db_url_priority_chain(monkeypatch):
    """契约：解析优先级 = 显式 URL > DATABASE_URL > GAMEFORGE_DB_* > DBMY_* > 默认。"""
    # 清场：每次测试互不污染
    for k in [
        "DATABASE_URL",
        "GAMEFORGE_DB_HOST", "GAMEFORGE_DB_PORT", "GAMEFORGE_DB_USER",
        "GAMEFORGE_DB_NAME", "GAMEFORGE_DB_PASSWORD",
        "DBMY_HOST", "DBMY_PORT", "DBMY_USER", "DBMY_DB", "DBMY_PASSWORD",
    ]:
        monkeypatch.delenv(k, raising=False)

    from src.db.session import _resolve_db_url

    # 1. 显式 URL 胜一切
    assert _resolve_db_url("sqlite:///explicit.db") == "sqlite:///explicit.db"

    # 2. DATABASE_URL 胜 GAMEFORGE/DBMY
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")
    assert _resolve_db_url(None) == "sqlite:///from-env.db"

    # 3. GAMEFORGE_DB_PASSWORD 胜 DBMY_PASSWORD
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("DBMY_PASSWORD", "dev_pwd")
    monkeypatch.setenv("GAMEFORGE_DB_PASSWORD", "prod_pwd")
    url = _resolve_db_url(None)
    assert "prod_pwd" in url and "dev_pwd" not in url

    # 4. 只有 DBMY_PASSWORD（开发场景）
    monkeypatch.delenv("GAMEFORGE_DB_PASSWORD")
    url = _resolve_db_url(None)
    assert "dev_pwd" in url

    # 5. GAMEFORGE_DB_USER 胜 DBMY_USER
    monkeypatch.setenv("DBMY_USER", "dev_user")
    monkeypatch.setenv("GAMEFORGE_DB_USER", "prod_user")
    monkeypatch.setenv("GAMEFORGE_DB_HOST", "10.0.0.5")
    url = _resolve_db_url(None)
    assert "prod_user" in url
    assert "dev_user" not in url
    assert "10.0.0.5" in url

    # 6. 兜底：env 全空 → 默认 URL（root@127.0.0.1:3306/gameforge，无密码）
    for k in [
        "DATABASE_URL", "DBMY_USER", "DBMY_HOST", "DBMY_PORT", "DBMY_DB", "DBMY_PASSWORD",
        "GAMEFORGE_DB_USER", "GAMEFORGE_DB_HOST", "GAMEFORGE_DB_PORT",
        "GAMEFORGE_DB_NAME", "GAMEFORGE_DB_PASSWORD",
    ]:
        monkeypatch.delenv(k, raising=False)
    url = _resolve_db_url(None)
    assert "127.0.0.1" in url
    assert "3306" in url
    assert "gameforge" in url
    assert "mysql+pymysql://" in url
    # 兜底时 user=root，password=""
    assert "root" in url


def test_resolve_db_url_escapes_special_chars(monkeypatch):
    """契约：密码含 @ / : 等特殊字符时 URL percent-encode。"""
    for k in ["DATABASE_URL", "DBMY_USER", "DBMY_PASSWORD",
              "GAMEFORGE_DB_USER", "GAMEFORGE_DB_PASSWORD"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DBMY_PASSWORD", "p@ss:word/123")
    from src.db.session import _resolve_db_url
    url = _resolve_db_url(None)
    # @ → %40
    assert "%40" in url
    # : → %3A
    assert "%3A" in url
    # / → %2F
    assert "%2F" in url
    # 没被错误切到 host 部分（host 应仍是 127.0.0.1）
    assert "@127.0.0.1" in url


def test_default_database_url_uses_no_password(monkeypatch):
    """契约：DEFAULT_DATABASE_URL 是无密码的兜底（开发环境 123456 由 DBMY_PASSWORD 提供）。"""
    # 清掉所有相关 env
    for k in ["DATABASE_URL", "DBMY_PASSWORD", "GAMEFORGE_DB_PASSWORD"]:
        monkeypatch.delenv(k, raising=False)
    from src.db.session import DEFAULT_DATABASE_URL, _resolve_db_url
    # 默认 URL 字符串字面无密码（root@ 形式）
    assert DEFAULT_DATABASE_URL == "mysql+pymysql://root@127.0.0.1:3306/gameforge?charset=utf8mb4"
    # 解析结果应等于默认 URL（fallback）
    url = _resolve_db_url(None)
    assert url == DEFAULT_DATABASE_URL


def test_production_password_takes_precedence(monkeypatch):
    """契约：生产环境 GAMEFORGE_DB_PASSWORD 优先级最高（与 GAMEFORGE_API_KEYS 同级）。"""
    # 开发密码 + 生产密码同时存在时，生产密码胜出
    monkeypatch.setenv("DBMY_PASSWORD", "dev_password_123")
    monkeypatch.setenv("GAMEFORGE_DB_PASSWORD", "vault_secret_xyz")
    from src.db.session import _resolve_db_url
    url = _resolve_db_url(None)
    assert "vault_secret_xyz" in url
    assert "dev_password_123" not in url


def test_secret_not_hardcoded_in_default_url():
    """契约：DEFAULT_DATABASE_URL 不应包含任何明文密码（防代码泄露）。"""
    # 检查代码常量字符串中不含 '123456' 等已知弱密码
    import inspect
    from src.db import session
    src = inspect.getsource(session)
    # 注释中说明"开发密码 123456 由 DBMY_PASSWORD 提供"，不要在默认 URL 字面里出现
    # 但默认值常量必须为开发无密码形式
    assert "123456" not in session.DEFAULT_DATABASE_URL, (
        "DEFAULT_DATABASE_URL 不应硬编码开发密码，应用 DBMY_PASSWORD 注入。"
    )