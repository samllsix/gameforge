"""GameForge - API服务器模块

提供RESTful API接口，支持高并发请求处理。
集成速率限制、并发控制、请求指标、安全防护等中间件。
"""

import os
import asyncio
import json
import yaml
import structlog
from contextlib import asynccontextmanager

logger = structlog.get_logger()
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, Response
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
from src.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    TaskPlanRequest,
    TaskPlanResponse,
    TaskStatusResponse,
)


def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    config_path = os.path.join(project_root, "config", "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


config = load_config()
API_VERSION = str(config.get("app", {}).get("version", "0.3.0"))
_sync_generation_semaphore = asyncio.Semaphore(2)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _split_env_list(name: str) -> Optional[List[str]]:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


IS_PRODUCTION = os.getenv("GAMEFORGE_ENV", "").lower() == "production"
SERVER_CONFIG = config.get("server", {})
DEFAULT_HOST = os.getenv("GAMEFORGE_HOST", str(SERVER_CONFIG.get("host", "127.0.0.1")))
DEFAULT_PORT = _env_int("GAMEFORGE_PORT", int(SERVER_CONFIG.get("port", 8000)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
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
        details={"version": API_VERSION},
    )

    # 初始化 GodotSupervisor（按需启动）
    try:
        from src.engine.godot import GodotSupervisor
        supervisor = await GodotSupervisor.get_instance(config)
        logger.info("preview.supervisor_ready", enabled=supervisor.enabled)
    except Exception as e:  # noqa: BLE001
        logger.warning("preview.supervisor_init_failed", error=str(e))

    # P2-5 LLM 启动探活 — 一次 ping，结果写到 app.state
    try:
        from src.utils.llm_health import ping as llm_ping
        llm_status = await llm_ping(config, timeout=5.0)
        app.state.llm_status = llm_status.to_dict()
        logger.info(
            "llm.startup_check",
            llm_configured=llm_status.llm_configured,
            ping_ok=llm_status.ping_ok,
            ping_error=llm_status.ping_error or None,
        )
    except Exception as e:  # noqa: BLE001
        app.state.llm_status = {
            "llm_configured": False,
            "ping_ok": None,
            "ping_error": f"health_check_crashed: {type(e).__name__}",
            "ping_latency_ms": 0.0,
        }
        logger.warning("llm.startup_check_crashed", error=str(e))

    yield

    # 退出时关闭所有 Godot 进程
    try:
        from src.engine.godot import GodotSupervisor
        if GodotSupervisor._instance is not None:
            await GodotSupervisor._instance.stop_all()
            logger.info("preview.supervisor_stopped")
    except Exception as e:  # noqa: BLE001
        logger.warning("preview.supervisor_stop_failed", error=str(e))


# 创建FastAPI应用
app = FastAPI(
    title="GameForge API",
    description="游戏研发全流程AI Agent协作平台 — 支持高并发与安全防护",
    version=API_VERSION,
    lifespan=lifespan,
)

# ========== 中间件注册（顺序很重要：后注册的先执行） ==========

# 1. 安全头（最外层）
app.add_middleware(SecurityHeadersMiddleware)

# 2. CORS（限制允许的源）
cors_config = get_secure_cors_config(_split_env_list("GAMEFORGE_CORS_ORIGINS"))
app.add_middleware(CORSMiddleware, **cors_config)

# 3. GZip 压缩（对 CSS/JS/HTML 响应压缩 ~70%）
app.add_middleware(GZipMiddleware, minimum_size=500)

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

ALLOW_INSECURE_LOCALHOST = (
    os.getenv("GAMEFORGE_ALLOW_INSECURE_LOCALHOST", "").lower() == "true"
)
if not API_KEYS and not ALLOW_INSECURE_LOCALHOST:
    raise RuntimeError(
        "GAMEFORGE_API_KEYS must be set. For loopback-only development, explicitly "
        "set GAMEFORGE_ALLOW_INSECURE_LOCALHOST=true."
    )

app.add_middleware(
    APIKeyAuthMiddleware,
    api_keys=API_KEYS,
    enabled=not ALLOW_INSECURE_LOCALHOST,
)


# ========== 静态文件和前端 ==========

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """为静态资源添加缓存头"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            # 开发阶段：每次验证，避免 HTML/JS 版本不一致
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path == "/app":
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

app.add_middleware(StaticCacheMiddleware)


@app.get("/app")
async def serve_frontend():
    """前端页面"""
    return FileResponse(
        os.path.join(_static_dir, "index.html"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/demo")
async def serve_demo():
    """Demo 演示页面"""
    return FileResponse(
        os.path.join(_static_dir, "demo.html"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# 挂载额外路由模块
from src.api.routes import router as routes_router
app.include_router(routes_router, prefix="/api/v1/ext", tags=["extended"])




# ========== 请求/响应模型（从schemas模块统一导入） ==========


# ========== 初始化 ==========

# ========== API路由 ==========


@app.get("/")
async def root():
    """根路径"""
    llm = getattr(app.state, "llm_status", None) or {
        "llm_configured": False, "ping_ok": None, "ping_error": "not_checked",
    }
    return {
        "name": "GameForge API",
        "version": API_VERSION,
        "description": "游戏研发全流程AI Agent协作平台 — 支持高并发与安全防护",
        "security": {
            "rate_limiting": "60 req/min/IP",
            "concurrency_limit": "20 concurrent",
            "input_validation": "enabled",
            "security_headers": "enabled",
            "api_key_auth": "enabled" if API_KEYS else "disabled",
        },
        "llm_configured": llm.get("llm_configured", False),
        "llm_ping_ok": llm.get("ping_ok"),
        "llm_ping_error": llm.get("ping_error") or "",
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    manager = await ConcurrencyManager.get_instance()
    stats = manager.get_stats()
    llm = getattr(app.state, "llm_status", None) or {}
    return {
        "status": "healthy",
        "concurrency": stats,
        "llm_configured": llm.get("llm_configured", False),
        "llm_ping_ok": llm.get("ping_ok"),
        "llm_ping_error": llm.get("ping_error") or "",
    }


@app.get("/stats")
async def get_stats():
    """获取系统统计信息"""
    manager = await ConcurrencyManager.get_instance()
    return {
        "concurrency": manager.get_stats(),
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus指标端点"""
    from src.utils.metrics import get_metrics, get_content_type
    metrics_data = get_metrics()
    if metrics_data is None:
        return Response(content="# prometheus-client not installed\n", media_type="text/plain")
    return Response(content=metrics_data, media_type=get_content_type())


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
        await _save_generation_history(payload, result)
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


async def _save_generation_history(payload: Dict[str, Any], result: Dict[str, Any]):
    """持久化生成历史到数据库（异步，不阻塞事件循环）

    供 generate_code / generate_code_sync / generate_code_stream 共用。
    """
    try:
        from src.db.session import _engine, run_db_sync
        from src.db.models import GenerationHistory
        if _engine is None:
            return

        def _do_save():
            from src.db.session import get_db
            db = get_db()
            try:
                history = GenerationHistory(
                    task_id=payload.get("task_id", ""),
                    engine=payload.get("engine", "unity"),
                    requirements=payload.get("requirements", ""),
                    files_generated=result.get("code_generated", {}),
                    task_count=len(result.get("task_plan", [])),
                    fix_count=len(result.get("fix_history", [])),
                    task_plan=result.get("task_plan"),
                    review_result=result.get("review_result"),
                    compile_result=result.get("compile_result"),
                    fix_history=result.get("fix_history"),
                    scene_description=result.get("scene_description"),
                    status="completed" if not result.get("error_log") else "failed",
                )
                db.add(history)
                db.commit()
            finally:
                db.close()

        await run_db_sync(_do_save)
    except Exception as e:
        logger.warning("history_save_failed", error=str(e))


@app.post("/api/v1/generate_sync", response_model=GenerateResponse)
async def generate_code_sync(request: GenerateRequest):
    """生成游戏代码（同步等待模式）"""
    try:
        async with _sync_generation_semaphore:
            workflow = create_workflow(config)
            payload = {
                "task_id": "",  # 同步模式无队列任务ID
                "engine": request.engine,
                "project_name": request.project_name,
                "requirements": request.requirements,
            }
            result = await workflow.run(
                {
                    "project_context": {
                        "engine": request.engine,
                        "project_name": request.project_name,
                        "requirements": request.requirements,
                    },
                }
            )
        # 持久化生成历史（与异步模式保持一致）
        await _save_generation_history(payload, result)
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
                "engine": "godot",
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
        plan_result = await planner.plan(state)
        tasks = plan_result.get("tasks", []) if isinstance(plan_result, dict) else plan_result
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
            {"name": "orchestrator", "description": "编排Agent - 任务调度和流程控制", "status": "available"},
            {"name": "planner", "description": "规划Agent - 解析需求并生成任务计划", "status": "available"},
            {"name": "code_generator", "description": "代码生成Agent - 生成游戏代码", "status": "available"},
            {"name": "code_reviewer", "description": "代码审查Agent - 审查代码质量", "status": "available"},
            {"name": "test_generator", "description": "测试生成Agent - 生成测试用例", "status": "available"},
            {"name": "debugger", "description": "调试Agent - 分析错误并生成修复方案", "status": "available"},
            {"name": "refactor", "description": "重构Agent - 分析代码质量并优化重构", "status": "available"},
            {"name": "reflector", "description": "反思Agent - 复盘运行并决定是否重规划（多智能体改造第二步）", "status": "available"},
            {"name": "scene_generator", "description": "场景生成Agent - 生成 Godot 场景", "status": "available"},
            {"name": "main_reviewer", "description": "主审查Agent - 终审与设计审查", "status": "available"},
        ]
    }


@app.post("/api/v1/debug/feature")
async def debug_feature(request: Dict[str, Any]):
    """调试端点：在浏览器中实测多智能体改造的每一项新能力（无需 LLM / 无需 Godot）。

    请求体：{"feature": "reflect" | "bus" | "delegate" | "engine", "state": {...可选覆盖}}
    仅用于验证功能，不参与真实生成流水线。
    """
    feature = request.get("feature")
    state = request.get("state", {}) or {}

    # 构造最小可用 state
    base_state = {
        "task_plan": state.get("task_plan", []),
        "error_log": state.get("error_log", []),
        "warnings": state.get("warnings", []),
        "main_review_result": state.get("main_review_result", {}),
        "design_review_result": state.get("design_review_result", {}),
        "validation_result": state.get("validation_result", {}),
        "code_generated": state.get("code_generated", {}),
        "message_bus": state.get("message_bus", []),
    }

    if feature == "reflect":
        from src.agents.reflector import ReflectorAgent
        agent = ReflectorAgent(config)
        result = await agent.execute(base_state)
        return {"feature": "reflect", "reflection": result}

    if feature == "bus":
        from src.core.state.bus import publish, messages_for, latest
        # 模拟 main_reviewer 发布一条重规划消息，debugger 读取
        bus_state = dict(base_state)
        pub = publish("replan", sender="main_reviewer", content="设计有坑，建议重排", recipient="planner")
        bus_state["message_bus"] = bus_state.get("message_bus", []) + pub["message_bus"]
        received = messages_for(bus_state, topic="replan", recipient="planner")
        return {
            "feature": "bus",
            "published": pub["message_bus"][0],
            "planner_inbox": received,
            "latest_for_planner": latest(bus_state, recipient="planner"),
        }

    if feature == "delegate":
        from src.agents.debugger import DebuggerAgent
        agent = DebuggerAgent(config)
        error = state.get("error", "Parser Error: Expected ':' in godot script at line 12")
        result = agent.delegate_to_research(error)
        return {"feature": "delegate", "result": result}

    if feature == "engine":
        from src.agents.test_generator import TestGeneratorAgent
        agent = TestGeneratorAgent(config)
        result = await agent.read_engine_feedback()
        return {"feature": "engine", "result": result}

    raise HTTPException(status_code=400, detail=f"未知 feature: {feature}")



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
    from src.db.session import get_db, run_db_sync
    from src.db.models import TaskRecord

    def _query():
        db = get_db()
        try:
            query = db.query(TaskRecord).order_by(TaskRecord.created_at.desc())
            if status:
                query = query.filter(TaskRecord.status == status)
            records = query.limit(min(limit, 200)).all()
            return [r.to_dict() for r in records]
        finally:
            db.close()

    records = await run_db_sync(_query)
    return {
        "tasks": records,
        "total": len(records),
    }


@app.get("/api/v1/history")
async def get_generation_history(limit: int = 20):
    """获取代码生成历史"""
    from src.db.session import get_db, run_db_sync
    from src.db.models import GenerationHistory

    def _query():
        db = get_db()
        try:
            records = db.query(GenerationHistory).order_by(
                GenerationHistory.created_at.desc()
            ).limit(min(limit, 100)).all()
            return [r.to_dict() for r in records]
        finally:
            db.close()

    records = await run_db_sync(_query)
    return {
        "history": records,
        "total": len(records),
    }


@app.get("/api/v1/history/{history_id}")
async def get_generation_history_detail(history_id: int):
    """获取单条生成历史详情（按ID）"""
    from src.db.session import get_db, run_db_sync
    from src.db.models import GenerationHistory

    def _query():
        db = get_db()
        try:
            return db.query(GenerationHistory).filter(GenerationHistory.id == history_id).first()
        finally:
            db.close()

    record = await run_db_sync(_query)
    if not record:
        raise HTTPException(status_code=404, detail=f"历史记录 {history_id} 不存在")
    return record.to_detail_dict()


@app.get("/api/v1/history/by_task/{task_id}")
async def get_history_by_task_id(task_id: str):
    """获取生成历史详情（按task_id）"""
    if not task_id.isalnum() or len(task_id) > 20:
        raise HTTPException(status_code=400, detail="无效的任务ID格式")

    from src.db.session import get_db, run_db_sync
    from src.db.models import GenerationHistory

    def _query():
        db = get_db()
        try:
            return db.query(GenerationHistory).filter(
                GenerationHistory.task_id == task_id
            ).order_by(GenerationHistory.created_at.desc()).first()
        finally:
            db.close()

    record = await run_db_sync(_query)
    if not record:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 的历史记录不存在")
    return record.to_detail_dict()


# ========== 实时预览：让前端轮询拿到 Godot 渲染出的游戏画面 ==========

import os as _os_preview
import re as _re_preview
import time as _time_preview
import mimetypes as _mimetypes_preview
from pathlib import Path as _Path_preview

_PREVIEW_PROJECT_RE = _re_preview.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")


def _resolve_preview_project(project_id: str) -> str:
    """把 project_id 解析为 projects/<project_id> 绝对路径，校验防路径穿越。"""
    if not project_id or not _PREVIEW_PROJECT_RE.match(project_id):
        raise HTTPException(status_code=400, detail="project_id 非法（仅允许字母数字_-.)")
    projects_root = _projects_root().resolve()
    abs_root = (projects_root / project_id).resolve()
    # 防止 project_id 含 ".." 越界：resolved 必须仍在 projects 根下
    try:
        abs_root.relative_to(projects_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="project_id 越出 projects 目录")
    return str(abs_root)


def _projects_root() -> _Path_preview:
    """返回仓库内 projects/ 目录的绝对路径。"""
    return _Path_preview(_os_preview.path.dirname(_os_preview.path.dirname(_os_preview.path.dirname(__file__)))) / "projects"


@app.get("/api/v1/preview/frame")
async def preview_frame(
    project_id: str,
    scene: Optional[str] = None,
    width: int = 640,
    height: int = 360,
    frame: int = 0,
):
    """用 Godot 长驻进程渲染指定项目指定场景，返回 PNG 字节流。

    2.0 流程：前端每 ~250ms 轮询一次
      1) 校验 project_id → 解析到 projects/<id> 绝对路径
      2) 通过 GodotSupervisor 确保 Godot 进程在跑（端口 8769 上常驻）
      3) HTTP GET http://127.0.0.1:8769/screenshot?frame=N 拿 PNG
      4) 加上 X-Preview-Source=godot-live 头返回浏览器
    1.0 fallback：通过 GODOT_PREVIEW_LEGACY_ONLY=1 仍可走一次性脚本。
    """
    project_path = _resolve_preview_project(project_id)

    preview_cfg = (config or {}).get("preview", {}) or {}
    legacy_only = _os_preview.environ.get("GAMEFORGE_PREVIEW_LEGACY_ONLY", "").lower() in {"1", "true", "yes"}
    legacy_only = legacy_only or bool(preview_cfg.get("legacy_only", False))

    # 2.0 自动建场景：如果项目下没有 project.godot + scenes/main.tscn，
    # 用 scene_to_godot 自动写一份富画面模板（含视差背景、玩家、敌人、金币、HUD、粒子）
    if not legacy_only:
        try:
            main_tscn = _os_preview.path.join(project_path, "scenes", "main.tscn")
            if not _os_preview.path.isfile(main_tscn):
                from src.engine.godot.scene_to_godot import (
                    default_scene_ir, write_project,
                )
                scene_ir = default_scene_ir(theme="sky_blue", genre="platformer")
                write_project(project_path, scene_ir, width=width, height=height)
                logger.info("preview.scene_auto_generated", project_id=project_id)
        except Exception as e:
            logger.warning("preview.scene_auto_gen_failed", error=str(e))

    if not _os_preview.path.isfile(_os_preview.path.join(project_path, "project.godot")):
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 缺少 project.godot")

    width = max(160, min(width, 1920))
    height = max(90, min(height, 1080))

    if not legacy_only:
        # 2.0 长驻进程路径：真窗口 + mss 截图
        from src.engine.godot import GodotSupervisor, GodotTimeout, GodotCrashed
        supervisor = await GodotSupervisor.get_instance(config)
        if not await supervisor.is_alive(project_id):
            try:
                await supervisor.start(project_id, project_path, scene_path=scene)
            except Exception as e:
                logger.warning("preview.supervisor_start_failed", project_id=project_id, error=str(e))
                raise HTTPException(status_code=502, detail=f"Godot 进程拉起起失败: {e}")

        try:
            png_bytes = await supervisor.get_frame(
                project_id, frame_index=frame, width=width, height=height,
            )
        except GodotTimeout as e:
            await supervisor.stop(project_id)
            raise HTTPException(status_code=504, detail=f"Godot 截图超时: {e}")
        except GodotCrashed as e:
            await supervisor.stop(project_id)
            raise HTTPException(status_code=502, detail=f"Godot 进程不可达: {e}")

        ts = int(_time_preview.time())
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "X-Preview-Frame": str(frame),
                "X-Preview-Width": str(width),
                "X-Preview-Height": str(height),
                "X-Preview-Timestamp": str(ts),
                "X-Preview-Source": "godot-mss",
            },
        )

    # ===== 1.0 兼容路径（一次性脚本） =====
    from src.engine.godot import GodotEditor

    # 默认场景：main.tscn -> GameScene.tscn -> 第一个 .tscn
    if not scene:
        candidates = ["res://scenes/main.tscn", "res://main.tscn", "res://scenes/GameScene.tscn"]
        for c in candidates:
            full = _os_preview.path.join(project_path, c.replace("res://", "").replace("/", _os_preview.sep))
            if _os_preview.path.isfile(full):
                scene = c
                break
        if not scene:
            scenes_dir = _os_preview.path.join(project_path, "scenes")
            if _os_preview.path.isdir(scenes_dir):
                for fn in _os_preview.listdir(scenes_dir):
                    if fn.lower().endswith(".tscn"):
                        scene = "res://scenes/" + fn
                        break
        if not scene:
            raise HTTPException(status_code=404, detail="项目中找不到 .tscn 场景文件")

    editor = GodotEditor(config)
    out_dir = _os_preview.path.join(project_path, "_preview_cache")
    _os_preview.makedirs(out_dir, exist_ok=True)
    out_path = _os_preview.path.join(out_dir, f"frame_{frame % 16:02d}.png")

    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()

    def _render():
        return editor.render_screenshot_frame(
            project_path=project_path,
            scene_path=scene,
            output_path=out_path,
            width=width,
            height=height,
            warmup_frames=8,
            frame_index=frame,
            timeout=30,
        )

    result = await loop.run_in_executor(None, _render)
    if not result.get("ok"):
        raise HTTPException(
            status_code=500,
            detail={"error": result.get("error", "渲染失败"), "stderr": (result.get("stderr") or "")[-400:]},
        )

    png_path = result["output_path"]
    if not _os_preview.path.isfile(png_path):
        raise HTTPException(status_code=500, detail="截图已声明成功但文件不存在")

    data = _Path_preview(png_path).read_bytes()
    ts = int(_time_preview.time())
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-Preview-Frame": str(frame),
            "X-Preview-Width": str(width),
            "X-Preview-Height": str(height),
            "X-Preview-Timestamp": str(ts),
            "X-Preview-Source": "godot-legacy",
        },
    )


@app.get("/api/v1/preview/stats")
async def preview_stats():
    """查看 supervisor 当前所有 Godot 进程状态"""
    from src.engine.godot import GodotSupervisor
    sup = await GodotSupervisor.get_instance(config)
    return sup.stats()


def start_server(host: Optional[str] = None, port: Optional[int] = None, workers: int = 1):
    """启动服务器"""
    resolved_host = host or DEFAULT_HOST
    if not API_KEYS and resolved_host not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("An unauthenticated GameForge API may only bind to loopback.")
    uvicorn.run(
        "src.api.main:app",
        host=resolved_host,
        port=port or DEFAULT_PORT,
        workers=workers,
        log_level="info",
    )


if __name__ == "__main__":
    start_server()
