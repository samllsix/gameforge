"""GameForge - 数据库会话管理

提供数据库引擎、会话工厂和初始化功能。
默认 MySQL（本地环境 mysql://root:123456@127.0.0.1:3306/gameforge）。
也支持 PostgreSQL / SQLite 通过 DATABASE_URL 切换。
"""

import asyncio
import functools
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, TypeVar

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv
from src.db.models import Base

load_dotenv()

_engine = None
_SessionLocal = None


# ---------------------------------------------------------------------------
# 数据库连接参数注入
# ---------------------------------------------------------------------------
# 风格与 src/api/main.py 的 GAMEFORGE_API_KEYS 一致：模块级常量、从 env 注入。
# 优先级（从高到低）：
#   1. 显式传入的 database_url / kwargs
#   2. DATABASE_URL 环境变量（完整 URL 直传，兼容旧配置）
#   3. GAMEFORGE_DB_PASSWORD + DBMY_PASSWORD（密码：生产密钥 > 开发明文）
#   4. GAMEFORGE_DB_USER / DBMY_USER（用户名）
#   5. GAMEFORGE_DB_HOST / DBMY_HOST（主机）
#   6. GAMEFORGE_DB_PORT / DBMY_PORT（端口）
#   7. GAMEFORGE_DB_NAME / DBMY_DB（数据库名）
#   8. 兜底默认值（开发态：localhost:3306 / root / 空密码 / gameforge）
#
# 生产环境推荐：
#   export GAMEFORGE_DB_PASSWORD='secret-from-vault'
#   # GAMEFORGE_DB_PASSWORD 优先级最高，运维/审计可识别
# 开发环境：
#   export DBMY_PASSWORD='123456'
#   # 或直接写在 .env（仅本机开发）

DEFAULT_DATABASE_URL = "mysql+pymysql://root@127.0.0.1:3306/gameforge?charset=utf8mb4"


def _resolve_db_url(explicit_url: str) -> str:
    """从 env 解析出最终 DATABASE_URL。

    优先级：显式 URL > DATABASE_URL > 组装 DBMY_* + GAMEFORGE_DB_* > 默认
    """
    if explicit_url:
        return explicit_url
    env_url = os.getenv("DATABASE_URL", "").strip()
    if env_url:
        return env_url

    # 从分散 env 拼装
    user = (
        os.getenv("GAMEFORGE_DB_USER")
        or os.getenv("DBMY_USER")
        or "root"
    )
    host = (
        os.getenv("GAMEFORGE_DB_HOST")
        or os.getenv("DBMY_HOST")
        or "127.0.0.1"
    )
    port = (
        os.getenv("GAMEFORGE_DB_PORT")
        or os.getenv("DBMY_PORT")
        or "3306"
    )
    name = (
        os.getenv("GAMEFORGE_DB_NAME")
        or os.getenv("DBMY_DB")
        or "gameforge"
    )
    # 密码优先级：GAMEFORGE_DB_PASSWORD > DBMY_PASSWORD > ""
    password = os.getenv("GAMEFORGE_DB_PASSWORD") or os.getenv("DBMY_PASSWORD") or ""

    # URL 转义：用户/密码含特殊字符（@ : /）必须 percent-encode
    # 空密码时省略冒号（与 DEFAULT_DATABASE_URL 风格一致）
    from urllib.parse import quote_plus
    user_enc = quote_plus(user)
    if password:
        pwd_enc = quote_plus(password)
        userinfo = f"{user_enc}:{pwd_enc}"
    else:
        userinfo = user_enc
    return (
        f"mysql+pymysql://{userinfo}@{host}:{port}/{name}?charset=utf8mb4"
    )


def _ensure_pymysql_registered():
    """PyMySQL 在 Python 3 不再默认注册为 MySQLdb；显式注册让 'mysql://' 也工作。

    这样做的好处：用户写 DATABASE_URL='mysql://root:...' 也能用，
    不必强制写 'mysql+pymysql://'。
    """
    try:
        import pymysql  # noqa: F401
        # 注册 PyMySQL 为 MySQLdb 替身，使 SQLAlchemy 'mysql://' URL 可用
        pymysql.install_as_MySQLdb()
    except ImportError:
        # PyMySQL 未装：让 SQLAlchemy 在运行时自己抛错（友好报错）
        pass


def get_engine(database_url: str = None):
    """获取数据库引擎（单例）

    优先级：
    1. 显式传入的 database_url
    2. DATABASE_URL 环境变量
    3. 默认 MySQL URL（本地开发环境：root:123456@127.0.0.1:3306/gameforge）
    4. 兜底 SQLite（仅在 MySQL/PostgreSQL 不可达时使用）

    URL scheme：
    - 'mysql://' 或 'mysql+pymysql://' → PyMySQL（纯 Python，无编译依赖）
    - 'postgresql://' 或 'postgresql+psycopg://' → psycopg2/3
    - 'sqlite:///' → SQLite
    """
    global _engine
    if _engine is None:
        url = _resolve_db_url(database_url)
        _ensure_pymysql_registered()

        connect_args: dict = {}
        engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}

        if url.startswith("sqlite"):
            # SQLite：单文件 + 多线程兼容
            os.makedirs(os.path.dirname(url.replace("sqlite:///", "")) or ".", exist_ok=True)
            connect_args["check_same_thread"] = False
        elif url.startswith("mysql"):
            # MySQL：长连接池 + utf8mb4
            connect_args["charset"] = "utf8mb4"
            engine_kwargs["pool_size"] = 5
            engine_kwargs["max_overflow"] = 10
            engine_kwargs["pool_recycle"] = 3600
            engine_kwargs["pool_timeout"] = 30
        else:
            # PostgreSQL / 其它：保持原有连接池配置
            engine_kwargs["pool_size"] = 5
            engine_kwargs["max_overflow"] = 10
            engine_kwargs["pool_recycle"] = 3600
            engine_kwargs["pool_timeout"] = 30

        _engine = create_engine(url, connect_args=connect_args, **engine_kwargs)
    return _engine


def get_session_factory():
    """获取会话工厂（单例）"""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(bind=engine)
    return _SessionLocal


def init_db(database_url: str = None):
    """初始化数据库（创建所有表）"""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)


def get_db() -> Session:
    """获取数据库会话"""
    factory = get_session_factory()
    return factory()


def reset_db():
    """重置数据库连接（用于测试）"""
    global _engine, _SessionLocal
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


# ---------------------------------------------------------------------------
# 异步上下文下的同步 DB 操作包装
# ---------------------------------------------------------------------------
# 原问题：在 async 函数中直接调用 db.commit() / db.query() 等同步阻塞 I/O
# 会阻塞整个 asyncio 事件循环，导致高并发下其他协程被饿死。
# 解决方案：用 asyncio.to_thread 把同步 DB 操作放到默认线程池执行。

T = TypeVar("T")


async def run_db_sync(func: Callable[[], T]) -> T:
    """在线程池中执行同步 DB 操作，避免阻塞事件循环

    用法：
        async def endpoint():
            def _do():
                db = get_db()
                try:
                    return db.query(TaskRecord).all()
                finally:
                    db.close()
            records = await run_db_sync(_do)
    """
    return await asyncio.to_thread(func)


@asynccontextmanager
async def get_db_async() -> AsyncIterator[Session]:
    """异步获取 DB 会话，自动在线程池中关闭

    用法：
        async def endpoint():
            async with get_db_async() as db:
                records = await run_db_sync(lambda: db.query(TaskRecord).all())
    """
    db = await asyncio.to_thread(get_db)
    try:
        yield db
    finally:
        await asyncio.to_thread(db.close)
