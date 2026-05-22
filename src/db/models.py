"""GameForge - 数据库模型定义

使用SQLAlchemy 2.0 DeclarativeBase定义ORM模型。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, Integer, Float, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    """任务记录"""
    __tablename__ = "tasks"

    id = Column(String(20), primary_key=True)
    task_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    payload = Column(JSON, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    priority = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "task_type": self.task_type,
            "status": self.status,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "priority": self.priority,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class GenerationHistory(Base):
    """代码生成历史"""
    __tablename__ = "generation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(20), nullable=False, index=True)
    engine = Column(String(20), nullable=False)
    requirements = Column(Text, nullable=False)
    files_generated = Column(JSON, default=dict)
    task_count = Column(Integer, default=0)
    fix_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Phase 4.6: 完整历史数据
    task_plan = Column(JSON, nullable=True)
    review_result = Column(JSON, nullable=True)
    compile_result = Column(JSON, nullable=True)
    fix_history = Column(JSON, nullable=True)
    scene_description = Column(JSON, nullable=True)
    status = Column(String(20), default="completed")

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "engine": self.engine,
            "task_count": self.task_count,
            "fix_count": self.fix_count,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_detail_dict(self):
        return {
            **self.to_dict(),
            "requirements": self.requirements,
            "files_generated": self.files_generated,
            "task_plan": self.task_plan,
            "review_result": self.review_result,
            "compile_result": self.compile_result,
            "fix_history": self.fix_history,
            "scene_description": self.scene_description,
        }


class AuditLog(Base):
    """审计日志"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    client_ip = Column(String(50))
    path = Column(String(200))
    method = Column(String(10))
    details = Column(JSON, default=dict)
    severity = Column(String(20), default="INFO")
    created_at = Column(DateTime, default=datetime.utcnow)
