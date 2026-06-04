"""测试数据库模型和会话"""

import pytest
from datetime import UTC, datetime
from pathlib import Path

from src.db.models import TaskRecord, GenerationHistory, AuditLog, Base
from src.db.session import get_engine, init_db, get_db, reset_db


@pytest.fixture(autouse=True)
def clean_db(tmp_path):
    """每个测试使用独立的SQLite数据库"""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    reset_db()
    if db_path.exists():
        db_path.unlink()
    init_db(db_url)
    yield
    reset_db()


class TestTaskRecord:
    """任务记录测试"""

    def test_create_task(self):
        db = get_db()
        try:
            task = TaskRecord(
                id="abc12345",
                task_type="workflow",
                status="pending",
                payload={"requirements": "test"},
                priority=0,
            )
            db.add(task)
            db.commit()

            result = db.query(TaskRecord).filter(TaskRecord.id == "abc12345").first()
            assert result is not None
            assert result.task_type == "workflow"
            assert result.status == "pending"
            assert result.payload == {"requirements": "test"}
        finally:
            db.close()

    def test_task_to_dict(self):
        task = TaskRecord(
            id="abc12345",
            task_type="workflow",
            status="completed",
            result={"code_generated": {}},
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
        d = task.to_dict()
        assert d["id"] == "abc12345"
        assert d["status"] == "completed"
        assert d["created_at"] == "2026-01-01T12:00:00"

    def test_task_status_update(self):
        db = get_db()
        try:
            task = TaskRecord(id="def67890", task_type="generate", status="pending")
            db.add(task)
            db.commit()

            task.status = "running"
            task.started_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()

            result = db.query(TaskRecord).filter(TaskRecord.id == "def67890").first()
            assert result.status == "running"
            assert result.started_at is not None
        finally:
            db.close()


class TestGenerationHistory:
    """生成历史测试"""

    def test_create_history(self):
        db = get_db()
        try:
            history = GenerationHistory(
                task_id="task001",
                engine="unity",
                requirements="创建一个2D平台游戏",
                files_generated={"Player.cs": "class Player {}"},
                task_count=3,
                fix_count=0,
            )
            db.add(history)
            db.commit()

            result = db.query(GenerationHistory).first()
            assert result is not None
            assert result.engine == "unity"
            assert result.task_count == 3
        finally:
            db.close()

    def test_history_to_dict(self):
        history = GenerationHistory(
            id=1,
            task_id="task001",
            engine="unity",
            requirements="test",
            task_count=3,
            fix_count=1,
            created_at=datetime(2026, 1, 1),
        )
        d = history.to_dict()
        assert d["id"] == 1
        assert d["engine"] == "unity"
        assert d["fix_count"] == 1


class TestAuditLog:
    """审计日志测试"""

    def test_create_audit_log(self):
        db = get_db()
        try:
            log = AuditLog(
                event_type="auth_attempt",
                client_ip="127.0.0.1",
                path="/api/v1/generate",
                method="POST",
                details={"success": True},
                severity="INFO",
            )
            db.add(log)
            db.commit()

            result = db.query(AuditLog).first()
            assert result is not None
            assert result.event_type == "auth_attempt"
            assert result.severity == "INFO"
        finally:
            db.close()
