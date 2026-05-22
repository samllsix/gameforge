"""GameForge - 数据库会话管理

提供数据库引擎、会话工厂和初始化功能。
支持PostgreSQL（生产）和SQLite（开发模式默认）。
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv
from src.db.models import Base

load_dotenv()

_engine = None
_SessionLocal = None


def get_engine(database_url: str = None):
    """获取数据库引擎（单例）

    优先级：
    1. 显式传入的 database_url
    2. DATABASE_URL 环境变量
    3. SQLite 默认（开发模式，开箱即用）
    """
    global _engine
    if _engine is None:
        url = database_url or os.getenv("DATABASE_URL", "")
        if not url:
            # 开发模式默认SQLite，无需外部数据库服务
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_dir = os.path.join(project_root, "data")
            os.makedirs(db_dir, exist_ok=True)
            url = f"sqlite:///{os.path.join(db_dir, 'gameforge.db')}"
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(url, echo=False, pool_pre_ping=True, connect_args=connect_args)
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
