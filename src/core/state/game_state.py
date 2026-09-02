"""GameForge - 游戏开发状态管理模块

定义了Multi-Agent协作过程中的状态数据结构。
"""

from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from operator import add
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime


def merge_dicts(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """合并两个字典，新值覆盖旧值（用于 code_generated 等字段）"""
    if old is None:
        return new or {}
    if new is None:
        return old
    return {**old, **new}


def append_to_bus(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """消息总线 reducer：列表追加（多智能体改造第三步）"""
    if old is None:
        return new or []
    if new is None:
        return old
    return old + new


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskType(str, Enum):
    """任务类型枚举"""
    CODE = "code"
    TEST = "test"
    ART = "art"
    DESIGN = "design"
    REVIEW = "review"
    FIX = "fix"
    SCENE = "scene"
    UI = "ui"
    CONFIG = "config"
    DOCUMENTATION = "documentation"


class AgentType(str, Enum):
    """Agent类型枚举"""
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    GAME_DESIGNER = "game_designer"
    CODE_GENERATOR = "code_generator"
    CODE_REVIEWER = "code_reviewer"
    TEST_GENERATOR = "test_generator"
    DEBUGGER = "debugger"
    REFACTOR = "refactor"
    SCENE_GENERATOR = "scene_generator"
    MAIN_REVIEWER = "main_reviewer"


class Task(BaseModel):
    """任务数据模型"""
    id: str = Field(..., description="任务唯一标识")
    name: str = Field(..., description="任务名称")
    description: str = Field(..., description="任务详细描述")
    type: TaskType = Field(..., description="任务类型")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    priority: int = Field(default=0, description="任务优先级")
    dependencies: List[str] = Field(default_factory=list, description="依赖任务ID列表")
    assigned_agent: Optional[AgentType] = Field(None, description="分配的Agent")
    result: Optional[Dict[str, Any]] = Field(None, description="任务执行结果")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")


class CodeArtifact(BaseModel):
    """代码产物数据模型"""
    file_path: str = Field(..., description="文件路径")
    content: str = Field(..., description="代码内容")
    language: str = Field(..., description="编程语言")
    engine: str = Field(..., description="游戏引擎")
    version: int = Field(default=1, description="版本号")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")


class TestResult(BaseModel):
    __test__ = False

    """测试结果数据模型"""
    test_name: str = Field(..., description="测试名称")
    passed: bool = Field(..., description="是否通过")
    execution_time: float = Field(..., description="执行时间(秒)")
    error_message: Optional[str] = Field(None, description="错误信息")
    stack_trace: Optional[str] = Field(None, description="堆栈跟踪")


class TestReport(BaseModel):
    __test__ = False

    """测试报告数据模型"""
    total_tests: int = Field(..., description="总测试数")
    passed_tests: int = Field(..., description="通过测试数")
    failed_tests: int = Field(..., description="失败测试数")
    success_rate: float = Field(..., description="成功率")
    execution_time: float = Field(..., description="总执行时间")
    results: List[TestResult] = Field(default_factory=list, description="测试结果列表")


class FixRecord(BaseModel):
    """修复记录数据模型"""
    error_type: str = Field(..., description="错误类型")
    error_message: str = Field(..., description="错误信息")
    file_path: str = Field(..., description="错误文件路径")
    line_number: Optional[int] = Field(None, description="错误行号")
    fix_description: str = Field(..., description="修复描述")
    fix_code: str = Field(..., description="修复代码")
    success: bool = Field(..., description="修复是否成功")
    created_at: datetime = Field(default_factory=datetime.now, description="修复时间")


class GameDevState(TypedDict):
    """游戏开发状态 - LangGraph状态图核心数据结构

    使用 Annotated 类型标记 reducer：
    - list + add: 多节点返回值自动追加合并（error_log, warnings, fix_history, code_artifacts）
    - dict + merge_dicts: 多节点返回值自动字典合并（code_generated）
    - 普通类型: 后写入覆盖前值（current_phase, current_task_id 等）
    """
    # 任务规划
    task_plan: List[Dict[str, Any]]
    current_task_id: Optional[str]

    # 品类智能匹配结果（genre_specs.match_genre 的产物，供 SSE 推送与下游生成参考）
    genre_match: Optional[Dict[str, Any]]

    # 代码生成 — reducer: 字典合并
    code_generated: Annotated[Dict[str, str], merge_dicts]
    code_artifacts: Annotated[List[Dict[str, Any]], add]

    # 测试结果
    test_results: Optional[Dict[str, Any]]
    test_report: Optional[Dict[str, Any]]

    # 修复历史 — reducer: 列表追加
    fix_history: Annotated[List[Dict[str, Any]], add]
    fix_attempts: int

    # 流程控制
    current_phase: str
    is_complete: bool
    requires_human_input: bool
    ready_task_ids: Optional[List[str]]

    # 上下文信息
    project_context: Dict[str, Any]
    error_log: Annotated[List[str], add]

    # 场景生成
    scene_description: Optional[Dict[str, Any]]
    scene_status: str
    scene_error: Optional[str]

    # Game Design Model — 游戏整体设计模型
    game_design_model: Optional[Dict[str, Any]]

    # 代码元数据
    file_metadata: Dict[str, Any]

    # 一致性校验
    validation_result: Optional[Dict[str, Any]]

    # 主审查：代码二次审查与游戏设计审查
    main_review_result: Optional[Dict[str, Any]]
    design_review_result: Optional[Dict[str, Any]]

    # 警告 — reducer: 列表追加
    warnings: Annotated[List[str], add]

    # 沙箱平台（Phase 1 集成）
    sandbox: Optional[Dict[str, Any]]

    # 消息总线（多智能体改造第三步）：发布-订阅，解耦硬编码边
    message_bus: Annotated[List[Dict[str, Any]], append_to_bus]


class ProjectContext(BaseModel):
    """项目上下文信息"""
    project_name: str = Field(..., description="项目名称")
    engine: str = Field(..., description="游戏引擎")
    unity_version: Optional[str] = Field(None, description="Unity版本")
    unreal_version: Optional[str] = Field(None, description="Unreal版本")
    coding_standards: Dict[str, Any] = Field(default_factory=dict, description="编码规范")
    architecture_patterns: List[str] = Field(default_factory=list, description="架构模式")
    dependencies: List[str] = Field(default_factory=list, description="项目依赖")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="其他元数据")
