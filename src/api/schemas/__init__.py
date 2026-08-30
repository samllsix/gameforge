"""GameForge - API数据模型模块

定义请求和响应的Pydantic模型。
"""

from pydantic import BaseModel, Field, field_validator
from typing import Dict, Any, List, Optional


class GenerateRequest(BaseModel):
    """代码生成请求"""
    requirements: str = Field(..., description="游戏需求描述")
    engine: str = Field(default="godot", description="游戏引擎 (godot)")
    project_name: str = Field(default="GameForge Project", description="项目名称")

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, v):
        from src.api.security import InputValidator
        result = InputValidator.validate_requirements(v)
        if not result["valid"]:
            raise ValueError(result["error"])
        return result["sanitized"]

    @field_validator("engine")
    @classmethod
    def validate_engine(cls, v):
        from src.api.security import InputValidator
        result = InputValidator.validate_engine(v)
        if not result["valid"]:
            raise ValueError(result["error"])
        return v.lower()

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v):
        from src.api.security import InputValidator
        result = InputValidator.validate_project_name(v)
        if not result["valid"]:
            raise ValueError(result["error"])
        return v


class GenerateResponse(BaseModel):
    """代码生成响应"""
    success: bool
    task_id: Optional[str] = None
    code_generated: Dict[str, str] = {}
    task_count: int = 0
    fix_count: int = 0
    message: str = ""


class TaskPlanRequest(BaseModel):
    """任务规划请求"""
    requirements: str = Field(..., description="游戏需求描述")

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, v):
        from src.api.security import InputValidator
        result = InputValidator.validate_requirements(v)
        if not result["valid"]:
            raise ValueError(result["error"])
        return result["sanitized"]


class TaskPlanResponse(BaseModel):
    """任务规划响应"""
    success: bool
    task_id: Optional[str] = None
    tasks: List[Dict[str, Any]] = []
    message: str = ""


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CompileRequest(BaseModel):
    """编译请求"""
    project_path: Optional[str] = Field(default=None, description="Godot项目路径")


class CompileResponse(BaseModel):
    """编译响应"""
    success: bool
    errors: List[str] = []
    warnings: List[str] = []
    compile_time: float = 0.0


class ImportRequest(BaseModel):
    """文件导入请求"""
    files: Dict[str, str] = Field(..., description="文件字典 {路径: 内容}")
    project_path: Optional[str] = Field(default=None, description="Godot项目路径")

    @field_validator("files")
    @classmethod
    def validate_file_paths(cls, v: Dict[str, str]) -> Dict[str, str]:
        """校验每个文件路径：拒绝空路径、路径穿越、绝对路径，只允许项目内相对路径"""
        from src.api.security import InputValidator

        if not v:
            raise ValueError("文件字典不能为空")
        for raw_path in v:
            if not raw_path or not raw_path.strip():
                raise ValueError("文件路径不能为空")
            if InputValidator.check_path_traversal(raw_path):
                raise ValueError(f"文件路径包含路径穿越字符: {raw_path}")
            normalized = raw_path.replace("\\", "/").removeprefix("res://")
            if normalized.startswith(("/", "~")) or ":" in normalized:
                raise ValueError(f"文件路径必须是项目内相对路径: {raw_path}")
            if any(seg == ".." for seg in normalized.split("/")):
                raise ValueError(f"文件路径不允许包含 '..': {raw_path}")
        return v


class ImportResponse(BaseModel):
    """文件导入响应"""
    success: bool
    imported_files: List[str] = []
    failed_files: List[str] = []
    message: str = ""


class EvalRequest(BaseModel):
    """评测请求"""
    project_name: str = Field(default="default", description="项目名称")
    code_files: Optional[Dict[str, str]] = Field(default=None, description="代码文件")


class EvalResponse(BaseModel):
    """评测响应"""
    project_name: str
    overall_score: float
    metrics: List[Dict[str, Any]] = []
    report_path: Optional[str] = None


class HealthResponse(BaseModel):
    """健康检查响应（与 /health 实际返回对齐）"""
    status: str = "healthy"
    concurrency: Dict[str, Any] = {}
    llm_configured: bool = False
    llm_ping_ok: Optional[bool] = None
    llm_ping_error: str = ""


class AgentInfo(BaseModel):
    """Agent信息"""
    name: str
    description: str
    status: str = "available"


class AgentListResponse(BaseModel):
    """Agent列表响应"""
    agents: List[AgentInfo]
