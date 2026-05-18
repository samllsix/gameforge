"""GameForge - 数据库会话管理

提供数据库引擎、会话工厂和初始化功能。
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
    """获取数据库引擎（单例）"""
    global _engine
    if _engine is None:
        url = database_url or os.getenv("DATABASE_URL", "postgresql://gameforge:gameforge123@localhost:5433/gameforge")
        _engine = create_engine(url, echo=False, pool_pre_ping=True)
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
