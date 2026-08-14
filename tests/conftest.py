"""共享测试 fixtures"""

import asyncio
import pytest
from pathlib import Path
import tempfile
import sys
import os

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("LANGCHAIN_API_KEY", None)
os.environ.pop("LANGSMITH_API_KEY", None)

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 修复Windows + Python 3.13的ProactorEventLoop socket权限问题
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _close_loggers():
    """每个测试后关闭所有 GameForgeLogger 的文件句柄（防止 Windows 文件锁）"""
    yield
    try:
        import logging
        from src.utils.logger import reset_logger
        reset_logger()
        # 关闭所有 GameForge.* logger 的 handler
        for name in list(logging.Logger.manager.loggerDict.keys()):
            if name.startswith("GameForge."):
                logger = logging.getLogger(name)
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
    except Exception:
        pass


@pytest.fixture
def sample_config():
    """基础配置fixture"""
    return {
        "app": {"version": "0.1.0", "environment": "test"},
        "llm": {
            "default_model": "mimo-v2-flash",
            "orchestrator": {"model": "mimo-v2-flash", "temperature": 0.1},
            "code_generator": {"model": "mimo-v2-pro", "temperature": 0.3},
        },
        "agents": {
            "orchestrator": {},
            "code_generator": {"supported_engines": ["godot"]},
            "code_reviewer": {},
            "test_generator": {},
            "debugger": {},
        },
    }


@pytest.fixture
def sample_game_state():
    """基础游戏开发状态fixture"""
    from src.core.state.game_state import TaskStatus
    return {
        "task_plan": [
            {
                "id": "task-001",
                "name": "Player",
                "description": "创建玩家控制器，支持移动和跳跃",
                "type": "code",
                "status": TaskStatus.PENDING.value,
                "priority": 1,
                "dependencies": [],
                "assigned_agent": "code_generator",
            },
            {
                "id": "task-002",
                "name": "GameManager",
                "description": "创建游戏管理器，管理游戏状态",
                "type": "code",
                "status": TaskStatus.PENDING.value,
                "priority": 2,
                "dependencies": ["task-001"],
                "assigned_agent": "code_generator",
            },
            {
                "id": "task-003",
                "name": "Test",
                "description": "编写单元测试",
                "type": "test",
                "status": TaskStatus.PENDING.value,
                "priority": 3,
                "dependencies": ["task-001", "task-002"],
                "assigned_agent": "test_generator",
            },
        ],
        "current_task_id": None,
        "code_generated": {},
        "code_artifacts": [],
        "test_results": None,
        "test_report": None,
        "fix_history": [],
        "fix_attempts": 0,
        "current_phase": "initialized",
        "is_complete": False,
        "requires_human_input": False,
        "project_context": {
            "engine": "godot",
            "project_name": "TestPlatformer",
            "requirements": "2D平台跳跃游戏",
        },
        "error_log": [],
    }


@pytest.fixture
def completed_state(sample_game_state):
    """所有任务已完成的状态"""
    from src.core.state.game_state import TaskStatus
    state = sample_game_state.copy()
    state["task_plan"] = [
        {**t, "status": TaskStatus.COMPLETED.value}
        for t in state["task_plan"]
    ]
    return state


@pytest.fixture
def tmp_path(request):
    """每个测试独立的临时目录，落到项目内 .tmp/pytest/（避免 Windows 系统目录权限问题）"""
    t_id = request.node.nodeid.replace("/", os.sep).replace("\\", os.sep)
    t_id = t_id.replace(":", "_")
    path = PROJECT_ROOT / ".tmp" / "pytest" / t_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def temp_dir():
    """临时目录fixture"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


@pytest.fixture
def test_output_dir():
    """项目内测试输出目录fixture（持久化，便于调试）"""
    output_dir = Path(__file__).parent / "_test_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    yield output_dir
