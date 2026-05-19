"""GameForge - API服务器模块

提供RESTful API接口，支持高并发请求处理。
集成速率限制、并发控制、请求指标、安全防护等中间件。
"""

import os
import asyncio
import json
import yaml
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from typing import Dict, Any, List, Optional
import uvicorn

from src.core.graph.workflow import create_workflow
from src.core.concurrency import ConcurrencyManager
from src.api.middleware import (
    RateLimitMiddleware,
    ConcurrencyLimitMiddleware,
    RequestMetricsMiddleware,
)
from src.api.security import (
    InputValidator,
    SecurityHeadersMiddleware,
    RequestBodyLimitMiddleware,
    APIKeyAuthMiddleware,
    InputValidationMiddleware,
    get_secure_cors_config,
    get_audit_logger,
)


def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    config_path = os.path.join("config", "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


# 创建FastAPI应用
app = FastAPI(
    title="GameForge API",
    description="游戏研发全流程AI Agent协作平台 — 支持高并发与安全防护",
    version="0.3.0",
)

# ========== 中间件注册（顺序很重要：后注册的先执行） ==========

# 1. 安全头（最外层）
app.add_middleware(SecurityHeadersMiddleware)

# 2. CORS（限制允许的源）
cors_config = get_secure_cors_config()
app.add_middleware(CORSMiddleware, **cors_config)

# 3. 请求体大小限制 (2MB)
app.add_middleware(RequestBodyLimitMiddleware, max_size_bytes=2_097_152)

# 4. 输入验证（检测注入攻击）
app.add_middleware(InputValidationMiddleware)

# 5. 请求指标
app.add_middleware(RequestMetricsMiddleware, log_file="logs/api_metrics.jsonl")

# 6. 并发控制（限制同时处理20个请求）
app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=20)

# 7. 速率限制（每IP每分钟60个请求）
app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)

# 8. API密钥认证（默认关闭，通过环境变量启用）
API_KEYS = {}
if os.getenv("GAMEFORGE_API_KEYS"):
    # 格式: key1:name1,key2:name2
    for pair in os.getenv("GAMEFORGE_API_KEYS", "").split(","):
        if ":" in pair:
            key, name = pair.split(":", 1)
            API_KEYS[key.strip()] = name.strip()

app.add_middleware(
    APIKeyAuthMiddleware,
    api_keys=API_KEYS,
    enabled=bool(API_KEYS),
)


# ========== 静态文件和前端 ==========

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/app")
async def serve_frontend():
    """前端页面"""
    return FileResponse(os.path.join(_static_dir, "index.html"))


# 挂载额外路由模块
from src.api.routes import router as routes_router
app.include_router(routes_router, prefix="/api/v1/ext", tags=["extended"])


# ========== 请求/响应模型（带输入验证） ==========


class GenerateRequest(BaseModel):
    """代码生成请求"""

    requirements: str
    engine: str = "unity"
    project_name: str = "GameForge Project"

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, v):
        result = InputValidator.validate_requirements(v)
        if not result["valid"]:
            raise ValueError(result["error"])
        return result["sanitized"]

    @field_validator("engine")
    @classmethod
    def validate_engine(cls, v):
        result = InputValidator.validate_engine(v)
        if not result["valid"]:
            raise ValueError(result["error"])
        return v.lower()

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v):
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

    requirements: str

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, v):
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


# ========== 初始化 ==========

config = load_config()


@app.on_event("startup")
async def startup():
    """应用启动时初始化"""
    # 初始化数据库
    from src.db.session import init_db
    init_db()

    await ConcurrencyManager.get_instance(
        max_concurrent_workflows=5,
        max_concurrent_llm_calls=10,
        max_queue_size=100,
    )
    audit = get_audit_logger()
    await audit.log_event(
        event_type="server_startup",
        client_ip="system",
        path="/",
        method="SYSTEM",
        details={"version": "0.4.0"},
    )


# ========== API路由 ==========


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "GameForge API",
        "version": "0.3.0",
        "description": "游戏研发全流程AI Agent协作平台 — 支持高并发与安全防护",
        "security": {
            "rate_limiting": "60 req/min/IP",
            "concurrency_limit": "20 concurrent",
            "input_validation": "enabled",
            "security_headers": "enabled",
            "api_key_auth": "enabled" if API_KEYS else "disabled",
        },
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    manager = await ConcurrencyManager.get_instance()
    stats = manager.get_stats()
    return {
        "status": "healthy",
        "concurrency": stats,
    }


@app.get("/stats")
async def get_stats():
    """获取系统统计信息"""
    manager = await ConcurrencyManager.get_instance()
    return {
        "concurrency": manager.get_stats(),
    }


@app.post("/api/v1/generate", response_model=GenerateResponse)
async def generate_code(request: GenerateRequest):
    """生成游戏代码（异步队列模式）"""
    manager = await ConcurrencyManager.get_instance()
    audit = get_audit_logger()

    await audit.log_event(
        event_type="generate_request",
        client_ip="api",
        path="/api/v1/generate",
        method="POST",
        details={"engine": request.engine, "project": request.project_name},
    )

    async def _run_workflow(payload: Dict[str, Any]) -> Dict[str, Any]:
        workflow = create_workflow(config)
        result = await workflow.run(
            {
                "project_context": {
                    "engine": payload["engine"],
                    "project_name": payload["project_name"],
                    "requirements": payload["requirements"],
                },
            }
        )
        # 持久化生成历史
        try:
            from src.db.session import get_db, _engine
            from src.db.models import GenerationHistory
            if _engine is not None:
                db = get_db()
                try:
                    history = GenerationHistory(
                        task_id=payload.get("task_id", ""),
                        engine=payload.get("engine", "unity"),
                        requirements=payload.get("requirements", ""),
                        files_generated=result.get("code_generated", {}),
                        task_count=len(result.get("task_plan", [])),
                        fix_count=len(result.get("fix_history", [])),
                    )
                    db.add(history)
                    db.commit()
                finally:
                    db.close()
        except Exception:
            pass
        return result

    payload = {
        "requirements": request.requirements,
        "engine": request.engine,
        "project_name": request.project_name,
    }
    task_id = await manager.submit_task(
        task_type="workflow",
        payload=payload,
        handler=_run_workflow,
        priority=0,
    )
    payload["task_id"] = task_id

    return GenerateResponse(
        success=True,
        task_id=task_id,
        message=f"任务已提交，通过 /api/v1/task/{task_id} 查询进度",
    )


@app.post("/api/v1/generate_sync", response_model=GenerateResponse)
async def generate_code_sync(request: GenerateRequest):
    """生成游戏代码（同步等待模式）"""
    try:
        workflow = create_workflow(config)
        result = await workflow.run(
            {
                "project_context": {
                    "engine": request.engine,
                    "project_name": request.project_name,
                    "requirements": request.requirements,
                },
            }
        )
        return GenerateResponse(
            success=True,
            code_generated=result.get("code_generated", {}),
            task_count=len(result.get("task_plan", [])),
            fix_count=len(result.get("fix_history", [])),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/generate_stream")
async def generate_code_stream(request: GenerateRequest):
    """生成游戏代码（SSE流式返回）"""
    queue = asyncio.Queue()

    async def event_generator():
        workflow = create_workflow(config)

        async def callback(event_type: str, data: dict):
            await queue.put({"event": event_type, "data": data})

        async def run_workflow():
            try:
                await workflow.run_with_streaming(
                    {
                        "project_context": {
                            "engine": request.engine,
                            "project_name": request.project_name,
                            "requirements": request.requirements,
                        },
                    },
                    event_callback=callback,
                )
            except Exception as e:
                await queue.put({"event": "error", "data": {"message": str(e)}})
            finally:
                await queue.put(None)

        asyncio.create_task(run_workflow())

        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/plan", response_model=TaskPlanResponse)
async def plan_tasks(request: TaskPlanRequest):
    """规划任务"""
    try:
        from src.agents.planner import PlannerAgent

        planner = PlannerAgent(config)
        state = {
            "project_context": {
                "requirements": request.requirements,
                "engine": "unity",
            },
            "task_plan": [],
            "code_generated": {},
            "code_artifacts": [],
            "test_results": None,
            "test_report": None,
            "fix_history": [],
            "fix_attempts": 0,
            "current_phase": "planning",
            "is_complete": False,
            "requires_human_input": False,
            "error_log": [],
        }
        tasks = await planner.plan(state)
        return TaskPlanResponse(success=True, tasks=tasks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """查询任务状态"""
    # 验证task_id格式（防止注入）
    if not task_id.isalnum() or len(task_id) > 20:
        raise HTTPException(status_code=400, detail="无效的任务ID格式")

    manager = await ConcurrencyManager.get_instance()
    task = await manager.get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        result=task.result,
        error=task.error,
    )


@app.post("/api/v1/task/{task_id}/wait")
async def wait_for_task(task_id: str, timeout: int = 300):
    """等待任务完成"""
    if not task_id.isalnum() or len(task_id) > 20:
        raise HTTPException(status_code=400, detail="无效的任务ID格式")

    manager = await ConcurrencyManager.get_instance()
    task = await manager.wait_for_task(task_id, timeout=min(timeout, 600))
    if not task:
        raise HTTPException(status_code=408, detail="等待超时")

    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        result=task.result,
        error=task.error,
    )


@app.get("/api/v1/agents")
async def list_agents():
    """列出所有可用的Agent"""
    return {
        "agents": [
            {"name": "orchestrator", "description": "编排Agent - 任务调度和流程控制"},
            {"name": "planner", "description": "规划Agent - 解析需求并生成任务计划"},
            {"name": "code_generator", "description": "代码生成Agent - 生成游戏代码"},
            {"name": "code_reviewer", "description": "代码审查Agent - 审查代码质量"},
            {"name": "test_generator", "description": "测试生成Agent - 生成测试用例"},
            {"name": "debugger", "description": "调试Agent - 分析错误并生成修复方案"},
            {"name": "refactor", "description": "重构Agent - 分析代码质量并优化重构"},
        ]
    }


@app.get("/security/test")
async def security_test():
    """安全功能测试端点"""
    return {
        "security_features": {
            "input_validation": {
                "prompt_injection_detection": "enabled",
                "path_traversal_detection": "enabled",
                "sql_injection_detection": "enabled",
                "filename_sanitization": "enabled",
            },
            "middleware_stack": [
                "SecurityHeadersMiddleware",
                "CORSMiddleware (restricted origins)",
                "RequestBodyLimitMiddleware (2MB)",
                "InputValidationMiddleware",
                "RequestMetricsMiddleware",
                "ConcurrencyLimitMiddleware (20)",
                "RateLimitMiddleware (60/min/IP)",
                "APIKeyAuthMiddleware",
            ],
            "audit_logging": "enabled (logs/security/)",
            "api_key_auth": "enabled" if API_KEYS else "disabled (set GAMEFORGE_API_KEYS to enable)",
        }
    }


@app.get("/api/v1/tasks")
async def list_tasks(limit: int = 50, status: Optional[str] = None):
    """获取任务历史列表"""
    from src.db.session import get_db
    from src.db.models import TaskRecord

    db = get_db()
    try:
        query = db.query(TaskRecord).order_by(TaskRecord.created_at.desc())
        if status:
            query = query.filter(TaskRecord.status == status)
        records = query.limit(min(limit, 200)).all()
        return {
            "tasks": [r.to_dict() for r in records],
            "total": len(records),
        }
    finally:
        db.close()


@app.get("/api/v1/history")
async def get_generation_history(limit: int = 20):
    """获取代码生成历史"""
    from src.db.session import get_db
    from src.db.models import GenerationHistory

    db = get_db()
    try:
        records = db.query(GenerationHistory).order_by(
            GenerationHistory.created_at.desc()
        ).limit(min(limit, 100)).all()
        return {
            "history": [r.to_dict() for r in records],
            "total": len(records),
        }
    finally:
        db.close()


def start_server(host: str = "0.0.0.0", port: int = 8001, workers: int = 1):
    """启动服务器"""
    uvicorn.run(
        "src.api.main:app",
        host=host,
        port=port,
        workers=workers,
        log_level="info",
    )


if __name__ == "__main__":
    start_server()
