"""测试游戏开发状态模型"""

import pytest
from datetime import datetime
from src.core.state.game_state import (
    TaskStatus, TaskType, AgentType, Task, CodeArtifact,
    TestResult, TestReport, FixRecord, ProjectContext,
)


class TestEnums:
    def test_task_status_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.BLOCKED.value == "blocked"
        assert len(TaskStatus) == 5

    def test_task_type_values(self):
        assert TaskType.CODE.value == "code"
        assert TaskType.TEST.value == "test"
        assert TaskType.ART.value == "art"
        assert TaskType.DESIGN.value == "design"
        assert TaskType.REVIEW.value == "review"
        assert TaskType.FIX.value == "fix"
        assert TaskType.SCENE.value == "scene"
        assert TaskType.UI.value == "ui"
        assert TaskType.CONFIG.value == "config"
        assert TaskType.DOCUMENTATION.value == "documentation"
        assert len(TaskType) == 10

    def test_agent_type_values(self):
        assert AgentType.ORCHESTRATOR.value == "orchestrator"
        assert AgentType.PLANNER.value == "planner"
        assert AgentType.CODE_GENERATOR.value == "code_generator"
        assert AgentType.CODE_REVIEWER.value == "code_reviewer"
        assert AgentType.TEST_GENERATOR.value == "test_generator"
        assert AgentType.DEBUGGER.value == "debugger"
        assert AgentType.SCENE_GENERATOR.value == "scene_generator"
        assert len(AgentType) == 8


class TestTaskModel:
    def test_create_minimal_task(self):
        task = Task(id="t1", name="Test Task", description="desc", type=TaskType.CODE)
        assert task.id == "t1"
        assert task.status == TaskStatus.PENDING
        assert task.priority == 0
        assert task.dependencies == []

    def test_task_default_values(self):
        task = Task(id="t2", name="T2", description="d2", type=TaskType.TEST)
        assert task.assigned_agent is None
        assert task.result is None
        assert isinstance(task.created_at, datetime)
        assert isinstance(task.updated_at, datetime)

    def test_task_with_dependencies(self):
        task = Task(
            id="t3", name="T3", description="d3",
            type=TaskType.CODE, dependencies=["t1", "t2"],
            priority=5, assigned_agent=AgentType.CODE_GENERATOR,
        )
        assert task.dependencies == ["t1", "t2"]
        assert task.priority == 5
        assert task.assigned_agent == AgentType.CODE_GENERATOR

    def test_task_serialization(self):
        task = Task(id="t4", name="serial", description="test", type=TaskType.CODE)
        d = task.model_dump()
        assert d["id"] == "t4"
        assert d["status"] == "pending"
        assert "created_at" in d


class TestCodeArtifactModel:
    def test_create_artifact(self):
        art = CodeArtifact(
            file_path="Assets/Scripts/Test.cs",
            content="public class Test {}",
            language="csharp",
            engine="unity",
        )
        assert art.file_path == "Assets/Scripts/Test.cs"
        assert art.language == "csharp"
        assert art.version == 1

    def test_artifact_version_increment(self):
        art1 = CodeArtifact(
            file_path="test.cs", content="v1",
            language="csharp", engine="unity",
        )
        art2 = CodeArtifact(
            file_path="test.cs", content="v2",
            language="csharp", engine="unity", version=2,
        )
        assert art1.version == 1
        assert art2.version == 2


class TestTestResultModel:
    def test_passed_result(self):
        r = TestResult(test_name="test_move", passed=True, execution_time=0.5)
        assert r.passed is True
        assert r.error_message is None
        assert r.stack_trace is None

    def test_failed_result(self):
        r = TestResult(
            test_name="test_move",
            passed=False,
            execution_time=0.3,
            error_message="AssertionError: expected True, got False",
            stack_trace="  File ... line 42",
        )
        assert r.passed is False
        assert "AssertionError" in r.error_message


class TestTestReportModel:
    def test_create_report(self):
        results = [
            TestResult(test_name="t1", passed=True, execution_time=0.1),
            TestResult(test_name="t2", passed=False, execution_time=0.2),
        ]
        report = TestReport(
            total_tests=2, passed_tests=1, failed_tests=1,
            success_rate=0.5, execution_time=0.3, results=results,
        )
        assert report.total_tests == 2
        assert report.passed_tests == 1
        assert report.success_rate == 0.5
        assert len(report.results) == 2

    def test_empty_report(self):
        report = TestReport(
            total_tests=0, passed_tests=0, failed_tests=0,
            success_rate=1.0, execution_time=0.0,
        )
        assert report.results == []


class TestFixRecordModel:
    def test_create_fix(self):
        fix = FixRecord(
            error_type="NullReferenceException",
            error_message="Object reference not set",
            file_path="Assets/Scripts/Player.cs",
            line_number=42,
            fix_description="Added null check",
            fix_code="if (obj != null) { obj.DoSomething(); }",
            success=True,
        )
        assert fix.error_type == "NullReferenceException"
        assert fix.line_number == 42
        assert fix.success is True

    def test_failed_fix(self):
        fix = FixRecord(
            error_type="CompilationError",
            error_message="Unknown identifier",
            file_path="test.cs",
            fix_description="Attempted rename",
            fix_code="// fix failed",
            success=False,
        )
        assert fix.success is False
        assert fix.line_number is None


class TestProjectContextModel:
    def test_create_context(self):
        ctx = ProjectContext(
            project_name="MyGame",
            engine="unity",
            unity_version="2022.3",
            architecture_patterns=["MVC", "ECS"],
        )
        assert ctx.engine == "unity"
        assert ctx.unity_version == "2022.3"
        assert "MVC" in ctx.architecture_patterns
        assert ctx.unreal_version is None

    def test_context_defaults(self):
        ctx = ProjectContext(project_name="Test", engine="unreal")
        assert ctx.coding_standards == {}
        assert ctx.dependencies == []
        assert ctx.metadata == {}
