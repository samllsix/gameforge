"""GameForge - API服务器模块

提供RESTful API接口，支持高并发请求处理。
集成速率限制、并发控制、请求指标、安全防护等中间件。
"""

import os
import re
import json
import time
import asyncio
import contextlib
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Dict, Any, List, Optional

import yaml
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

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
from src.api.routes import router as routes_router
from src.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    TaskPlanRequest,
    TaskPlanResponse,
    TaskStatusResponse,
    HealthResponse,
    AgentListResponse,
)
from src.sandbox.controller import SandboxController

logger = structlog.get_logger()


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
        llm_status = await llm_ping(config, timeout=15.0)
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

# 4. 请求体大小限制 (2MB)
app.add_middleware(RequestBodyLimitMiddleware, max_size_bytes=2_097_152)

# 5. 输入验证（检测注入攻击）
app.add_middleware(InputValidationMiddleware)

# 6. 请求指标
app.add_middleware(RequestMetricsMiddleware, log_file="logs/api_metrics.jsonl")

# 7. 并发控制（限制同时处理20个请求）
app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=20)

# 8. 速率限制（每IP每分钟60个请求）
#    预览帧前端 ~250ms 轮询一次（≈240 req/min），远超全局限额，按前缀排除
app.add_middleware(
    RateLimitMiddleware,
    max_requests=60,
    window_seconds=60,
    exclude_prefixes=["/api/v1/preview/"],
)

# 9. API密钥认证（默认关闭，通过环境变量启用）
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

_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """为静态资源添加缓存头"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            # 开发阶段：每次验证，避免 HTML/JS 版本不一致
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

app.add_middleware(StaticCacheMiddleware)


# ========== 数字生命驾驶舱主入口 ==========

def _digital_life_html_path() -> str:
    """返回驾驶舱 HTML 的绝对路径。"""
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(project_root, "digital-life-system-spatial.html")


@app.get("/")
@app.get("/app")
@app.get("/dashboard")
@app.get("/digital")
async def serve_dashboard():
    """数字生命驾驶舱 SPA（接 SSE + /api/v1/preview/*）"""
    return FileResponse(
        _digital_life_html_path(),
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
app.include_router(routes_router, prefix="/api/v1/ext", tags=["extended"])


# ========== 统一错误信封 ==========
# 所有 JSON 错误响应统一为 {"error": <机器可读码>, "message": <人读信息>}，
# 与中间件 (_send_json_error) 的格式保持一致。

@app.exception_handler(HTTPException)
async def http_exception_to_envelope(request: Request, exc: HTTPException):
    try:
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        code = "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"error": code, "message": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_to_envelope(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "请求参数校验失败",
            "fields": [
                {"loc": list(err.get("loc", [])), "message": err.get("message", "")}
                for err in exc.errors()
            ],
        },
    )


# ========== 初始化 ==========

# ========== API路由 ==========


@app.get("/")
async def root():
    """根路径 — 数字生命驾驶舱入口（当文件存在时）"""
    if os.path.isfile(_digital_life_html_path()):
        return FileResponse(
            _digital_life_html_path(),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    return {"name": "GameForge API", "version": API_VERSION,
            "dashboard_entry": "/dashboard", "ui_hint": "open /dashboard"}


@app.get("/api-info")
async def api_info():
    """API 元信息（保留旧 / 等同行为）"""
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


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    manager = await ConcurrencyManager.get_instance()
    stats = manager.get_stats()
    llm = getattr(app.state, "llm_status", None) or {}
    return HealthResponse(
        status="healthy",
        concurrency=stats,
        llm_configured=llm.get("llm_configured", False),
        llm_ping_ok=llm.get("ping_ok"),
        llm_ping_error=llm.get("ping_error") or "",
    )


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


@app.post("/api/v1/generate", response_model=GenerateResponse, status_code=202)
async def generate_code(request: GenerateRequest, http_request: Request):
    """生成游戏代码（异步队列模式，返回 202 + Location 指向任务状态端点）"""
    manager = await ConcurrencyManager.get_instance()
    audit = get_audit_logger()
    client_ip = http_request.client.host if http_request.client else "unknown"

    await audit.log_event(
        event_type="generate_request",
        client_ip=client_ip,
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

    return JSONResponse(
        status_code=202,
        headers={"Location": f"/api/v1/task/{task_id}"},
        content=GenerateResponse(
            success=True,
            task_id=task_id,
            message=f"任务已提交，通过 /api/v1/task/{task_id} 查询进度",
        ).model_dump(),
    )


async def _save_generation_history(payload: Dict[str, Any], result: Dict[str, Any]):
    """持久化生成历史到数据库（异步，不阻塞事件循环）

    供 generate_code / generate_code_sync / generate_code_stream 共用。
    """
    try:
        from src.db.session import db_initialized, run_db_sync
        from src.db.models import GenerationHistory
        if not db_initialized():
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
    except HTTPException:
        raise
    except Exception:
        logger.exception("generate_sync_failed")
        raise HTTPException(status_code=500, detail="生成流程内部错误，请查看服务端日志")


@app.post("/api/v1/generate_stream")
async def generate_code_stream(request: GenerateRequest, http_request: Request):
    """生成游戏代码（SSE流式返回）

    客户端断开时会取消后台 workflow，避免资源泄漏；
    空闲期发送 `: ping` 注释心跳，防止代理断开长连接。
    """
    queue: asyncio.Queue = asyncio.Queue()

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

        # 保留任务引用，防止被垃圾回收；finally 中统一取消
        workflow_task = asyncio.create_task(run_workflow())

        try:
            while True:
                if await http_request.is_disconnected():
                    workflow_task.cancel()
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is None:
                    break
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        finally:
            if not workflow_task.done():
                workflow_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await workflow_task

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
    except Exception:
        logger.exception("plan_tasks_failed")
        raise HTTPException(status_code=500, detail="任务规划内部错误，请查看服务端日志")


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


@app.post("/api/v1/task/{task_id}/wait", response_model=TaskStatusResponse)
async def wait_for_task(
    task_id: str,
    timeout: int = Query(default=300, ge=1, le=600, description="最长等待秒数（1-600）"),
):
    """等待任务完成（长轮询）"""
    if not task_id.isalnum() or len(task_id) > 20:
        raise HTTPException(status_code=400, detail="无效的任务ID格式")

    manager = await ConcurrencyManager.get_instance()
    task = await manager.wait_for_task(task_id, timeout=timeout)
    if not task:
        # 504：服务端等待上游任务超时（408 语义是"客户端发请求太慢"，不适用）
        raise HTTPException(status_code=504, detail="等待任务完成超时")

    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        result=task.result,
        error=task.error,
    )


@app.get("/api/v1/agents", response_model=AgentListResponse)
async def list_agents():
    """列出所有可用的Agent"""
    agents = [
        {"name": "orchestrator", "description": "编排Agent - 任务调度和流程控制"},
        {"name": "planner", "description": "规划Agent - 解析需求并生成任务计划"},
        {"name": "code_generator", "description": "代码生成Agent - 生成游戏代码"},
        {"name": "code_reviewer", "description": "代码审查Agent - 审查代码质量"},
        {"name": "test_generator", "description": "测试生成Agent - 生成测试用例"},
        {"name": "debugger", "description": "调试Agent - 分析错误并生成修复方案"},
        {"name": "refactor", "description": "重构Agent - 分析代码质量并优化重构"},
        {"name": "scene_generator", "description": "场景生成Agent - 生成 Godot 场景"},
        {"name": "main_reviewer", "description": "主审查Agent - 终审与设计审查"},
    ]
    return AgentListResponse(agents=agents)


@app.post("/api/v1/debug/feature")
async def debug_feature(request: Dict[str, Any]):
    """调试端点：在浏览器中实测多智能体改造的每一项新能力（无需 LLM / 无需 Godot）。

    请求体：{"feature": "bus" | "delegate" | "engine", "state": {...可选覆盖}}
    仅用于验证功能，不参与真实生成流水线。生产环境返回 404。
    """
    if IS_PRODUCTION:
        raise HTTPException(status_code=404, detail="Not Found")

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
    """安全功能测试端点（生产环境返回 404，避免暴露安全配置）"""
    if IS_PRODUCTION:
        raise HTTPException(status_code=404, detail="Not Found")

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
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=200, description="返回条数（1-200）"),
    status: Optional[str] = Query(default=None, max_length=32),
):
    """获取任务历史列表"""
    from src.db.session import get_db, run_db_sync
    from src.db.models import TaskRecord

    def _query():
        db = get_db()
        try:
            query = db.query(TaskRecord).order_by(TaskRecord.created_at.desc())
            if status:
                query = query.filter(TaskRecord.status == status)
            records = query.limit(limit).all()
            return [r.to_dict() for r in records]
        finally:
            db.close()

    records = await run_db_sync(_query)
    return {
        "tasks": records,
        "total": len(records),
    }


@app.get("/api/v1/history")
async def get_generation_history(
    limit: int = Query(default=20, ge=1, le=100, description="返回条数（1-100）"),
):
    """获取代码生成历史"""
    from src.db.session import get_db, run_db_sync
    from src.db.models import GenerationHistory

    def _query():
        db = get_db()
        try:
            records = db.query(GenerationHistory).order_by(
                GenerationHistory.created_at.desc()
            ).limit(limit).all()
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

_PREVIEW_PROJECT_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")
# scene 必须是 res:// 开头的项目内相对路径（正则只允许安全字符，另显式拒绝 ".."）
_PREVIEW_SCENE_RE = re.compile(r"^res://[A-Za-z0-9_\-\.]+(/[A-Za-z0-9_\-\.]+)*$")

# 每项目构建锁：250ms 轮询叠加分钟级构建必须按项目串行化，否则并发请求
# 重复扣 AI 调用、清素材目录互相踩（P0-1）
_PREVIEW_BUILD_LOCKS: Dict[str, asyncio.Lock] = {}


class _PreviewBuiltByOtherRequest(Exception):
    """并发轮询时场景已被其他请求构建完成，当前请求跳过构建直接取帧。"""


def _resolve_preview_project(project_id: str, task_id: Optional[str] = None) -> str:
    """把 project_id 解析为项目绝对路径；提供 task_id 时解析到沙箱任务工作区。"""
    if not project_id or not _PREVIEW_PROJECT_RE.match(project_id):
        raise HTTPException(status_code=400, detail="project_id 非法（仅允许字母数字_-.)")
    if task_id is not None:
        task_dir = os.path.join("data", "sandbox", project_id, "tasks", task_id)
        abs_root = os.path.abspath(task_dir)
        if not os.path.isdir(abs_root):
            raise HTTPException(status_code=404, detail="沙箱任务工作区不存在")
        return abs_root
    projects_root = _projects_root().resolve()
    abs_root = (projects_root / project_id).resolve()
    # 防止 project_id 含 ".." 越界：resolved 必须仍在 projects 根下
    try:
        abs_root.relative_to(projects_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="project_id 越出 projects 目录")
    return str(abs_root)


def _projects_root() -> Path:
    """返回仓库内 projects/ 目录的绝对路径。"""
    return Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "projects"


def _load_project_scene_ir(project_path: str):
    """读取工作流落盘的 .scene_ir.json，返回 (SceneIR|None, requirements)。

    P0-1：预览优先使用工作流真实 Scene IR；文件缺失/损坏时返回 (None, "")，
    调用方回退 default_scene_ir（仅供无工作流的直接预览场景）。
    """
    ir_file = os.path.join(project_path, ".scene_ir.json")
    if not os.path.isfile(ir_file):
        return None, ""
    try:
        with open(ir_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        ir_data = payload.get("scene_ir") or {}
        from src.agents.scene_ir import SceneIR

        scene_ir = SceneIR(**ir_data)
        requirements = str(payload.get("requirements") or "")
        return scene_ir, requirements
    except Exception as e:
        logger.warning("preview.scene_ir_load_failed", error=str(e))
        return None, ""


@app.get("/api/v1/preview/frame")
async def preview_frame(
    project_id: str,
    scene: Optional[str] = None,
    width: int = Query(default=640, ge=160, le=1920),
    height: int = Query(default=360, ge=90, le=1080),
    frame: int = Query(default=0, ge=0),
    task_id: Optional[str] = Query(default=None),
):
    """用 Godot 长驻进程渲染指定项目指定场景，返回 PNG 字节流。

    2.0 流程：前端每 ~250ms 轮询一次
      1) 校验 project_id → 解析到 projects/<id> 绝对路径
      2) 通过 GodotSupervisor 确保 Godot 进程在跑（端口 8769 上常驻）
      3) HTTP GET http://127.0.0.1:8769/screenshot?frame=N 拿 PNG
      4) 加上 X-Preview-Source=godot-live 头返回浏览器
    1.0 fallback：通过 GODOT_PREVIEW_LEGACY_ONLY=1 仍可走一次性脚本。

    沙箱支持：提供 task_id 时，直接预览该沙箱任务工作区（未合并前）。
    """
    project_path = _resolve_preview_project(project_id, task_id=task_id)

    if scene is not None and (".." in scene or not _PREVIEW_SCENE_RE.match(scene)):
        raise HTTPException(status_code=400, detail="scene 非法（须为 res:// 开头的项目内相对路径）")

    preview_cfg = (config or {}).get("preview", {}) or {}
    legacy_only = os.environ.get("GAMEFORGE_PREVIEW_LEGACY_ONLY", "").lower() in {"1", "true", "yes"}
    legacy_only = legacy_only or bool(preview_cfg.get("legacy_only", False))

    # 2.0 自动建场景：如果项目下没有 project.godot + scenes/main.tscn，
    # 用 scene_to_godot 自动写一份场景（默认带 AI 生成的星露谷风像素素材，
    # 无 key / 关闭开关 / 失败时自动回退纯色块视觉），含视差背景、玩家、敌人、金币、HUD、粒子
    project_rebuilt = False
    if not legacy_only:
        try:
            def _need_build() -> bool:
                main_tscn = os.path.join(project_path, "scenes", "main.tscn")
                if not os.path.isfile(main_tscn):
                    return True
                ir_file = os.path.join(project_path, ".scene_ir.json")
                # IR 比场景新（工作流刚落盘）→ 重建，保证预览与需求一致
                return os.path.isfile(ir_file) and \
                    os.path.getmtime(ir_file) > os.path.getmtime(main_tscn)

            if _need_build():
                # 250ms 轮询 + 分钟级构建：必须按项目串行化，否则并发请求
                # 重复扣 AI 调用、清素材目录互相踩
                build_lock = _PREVIEW_BUILD_LOCKS.setdefault(project_id, asyncio.Lock())
                async with build_lock:
                    if not _need_build():
                        raise _PreviewBuiltByOtherRequest
                    from src.engine.godot.asset_forge import forge_assets
                    from src.engine.godot.scene_to_godot import (
                        default_scene_ir, write_project,
                    )

                    # P0-1：优先用工作流落盘的真实 Scene IR（需求一致），
                    # 仅在无 IR 文件时回退默认 IR（供无工作流的直接预览）
                    scene_ir, ir_requirements = _load_project_scene_ir(project_path)
                    if scene_ir is None:
                        scene_ir = default_scene_ir(theme="sky_blue", genre="platformer")
                    assets_on = (config or {}).get("assets", {}).get("ai_generated", True)

                    def _build_project_files() -> None:
                        # IR 更新触发的重建：清掉上一代 AI 素材缓存，避免旧主题素材复用
                        gen_dir = os.path.join(project_path, "assets", "gen")
                        if os.path.isdir(gen_dir):
                            import shutil

                            shutil.rmtree(gen_dir, ignore_errors=True)
                        # 主题驱动的美术指导书（一次 LLM 规划全部素材方向，失败回落母题模板）
                        from src.agents.art_director import plan_art

                        art_prompts = (
                            plan_art(scene_ir, requirements=ir_requirements) if assets_on else None
                        )
                        # forge_assets 内部有同项目锁 + 文件缓存，轮询重试不会重复扣 AI 调用
                        assets = forge_assets(scene_ir, project_path, art_prompts=art_prompts) if assets_on else {}
                        # 布局种子按 project_id 稳定散列：同项目重建布局一致，不同项目不重样
                        layout_seed = abs(hash(project_id)) % (2 ** 31)
                        write_project(project_path, scene_ir, width=width, height=height,
                                      assets=assets, layout_seed=layout_seed)

                        # 重建写入的新 PNG/WAV 必须重新 import：supervisor 只在 .godot
                        # 缺失时预导入，旧缓存 + 新素材会导致场景加载失败 → 预览崩溃循环
                        editor_path = (config or {}).get("godot", {}).get("editor_path", "") \
                            or os.getenv("GODOT_EDITOR_PATH", "")
                        if editor_path and os.path.isfile(editor_path):
                            from src.engine.godot.export_kit import ensure_imported

                            ensure_imported(project_path, editor_path)

                    await asyncio.to_thread(_build_project_files)
                    project_rebuilt = True
                    logger.info(
                        "preview.scene_auto_generated",
                        project_id=project_id,
                        source="workflow_ir" if os.path.isfile(
                            os.path.join(project_path, ".scene_ir.json")
                        ) else "default_ir",
                    )
        except _PreviewBuiltByOtherRequest:
            pass
        except Exception as e:
            logger.warning("preview.scene_auto_gen_failed", error=str(e))

    if not os.path.isfile(os.path.join(project_path, "project.godot")):
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 缺少 project.godot")

    if not legacy_only:
        # 2.0 长驻进程路径：真窗口 + mss 截图
        from src.engine.godot import GodotSupervisor, GodotTimeout, GodotCrashed
        supervisor = await GodotSupervisor.get_instance(config)
        if project_rebuilt:
            # 项目文件已重建：停掉跑旧场景的长驻进程，下面按新场景重新拉起
            await supervisor.stop(project_id)
        if not await supervisor.is_alive(project_id):
            try:
                await supervisor.start(project_id, project_path, scene_path=scene)
            except Exception as e:
                logger.warning("preview.supervisor_start_failed", project_id=project_id, error=str(e))
                raise HTTPException(status_code=502, detail=f"Godot 进程拉起失败，请稍后重试")

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

        ts = int(time.time())
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
            full = os.path.join(project_path, c.replace("res://", "").replace("/", os.sep))
            if os.path.isfile(full):
                scene = c
                break
        if not scene:
            scenes_dir = os.path.join(project_path, "scenes")
            if os.path.isdir(scenes_dir):
                for fn in os.listdir(scenes_dir):
                    if fn.lower().endswith(".tscn"):
                        scene = "res://scenes/" + fn
                        break
        if not scene:
            raise HTTPException(status_code=404, detail="项目中找不到 .tscn 场景文件")

    editor = GodotEditor(config)
    out_dir = os.path.join(project_path, "_preview_cache")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"frame_{frame % 16:02d}.png")

    loop = asyncio.get_running_loop()

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
    if not os.path.isfile(png_path):
        raise HTTPException(status_code=500, detail="截图已声明成功但文件不存在")

    data = Path(png_path).read_bytes()
    ts = int(time.time())
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
    return await sup.stats()


# ========== 一键发布：验收门禁 → 导出 → 可玩链接 ==========

_PLAY_MIME = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".wasm": "application/wasm",
    ".pck": "application/octet-stream",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".wav": "audio/wav",
    ".json": "application/json",
    ".ico": "image/x-icon",
}


def _resolve_editor_path() -> str:
    """解析 Godot 编辑器路径（与 supervisor 同源：config → env）。"""
    from src.engine.godot import _normalize_godot_path, _resolve_env

    godot_cfg = (config or {}).get("godot", {}) or {}
    return _normalize_godot_path(_resolve_env(
        godot_cfg.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
    ))


# ========== 灵感骰子：融合概念组合引擎 ==========

@app.get("/api/v1/concepts")
async def list_concepts(limit: int = Query(default=100, ge=1, le=1000)):
    """备用游戏概念库（基款×变体牌×主题包 组合排列，种子固定跨运行稳定）。"""
    from src.agents.genre_fusion import build_concept_library

    library = build_concept_library(count=limit)
    return {
        "count": len(library),
        "concepts": [
            {
                "index": i,
                "pitch": c.pitch,
                "primary": c.primary_id,
                "secondary": c.secondary_id,
                "twist": c.twist["name_zh"],
                "theme": c.theme_pack["name_zh"],
                "mechanics": c.spec.mechanics,
                "win": c.spec.win_condition,
            }
            for i, c in enumerate(library)
        ],
    }


@app.get("/api/v1/concepts/random")
async def random_concept(seed: Optional[int] = None):
    """灵感骰子：随机摇一个可执行游戏概念（带 seed 可复现）。"""
    from src.agents.genre_fusion import roll_concept

    c = roll_concept(seed)
    return {
        "pitch": c.pitch,
        "primary": c.primary_id,
        "secondary": c.secondary_id,
        "twist": c.twist["name_zh"],
        "theme": c.theme_pack["name_zh"],
        "mechanics": c.spec.mechanics,
        "extensions": c.spec.extensions,
        "win": c.spec.win_condition,
        "lose": c.spec.lose_condition,
        "hud_extras": c.spec.hud_extras,
        "camera": c.spec.camera,
    }


@app.post("/api/v1/projects/{project_id}/export")
async def export_project_api(
    project_id: str,
    preset: str = Query(default="Web", pattern="^(Web|Windows Desktop)$"),
):
    """一键发布：运行时冒烟门禁 → headless 导出 → 返回可玩链接。

    门禁不过（场景跑不起来）不出包，把结构化错误返回给调用方。
    """
    project_path = _resolve_preview_project(project_id)
    if not os.path.isfile(os.path.join(project_path, "scenes", "main.tscn")):
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 尚未生成场景")

    editor_path = _resolve_editor_path()
    if not editor_path or not os.path.isfile(editor_path):
        raise HTTPException(status_code=503, detail="Godot 编辑器未配置（GODOT_EDITOR_PATH）")

    def _gate_and_export() -> Dict[str, Any]:
        # 发布门禁 0：gd-guard 脚本安检（Rust 闸门，拦危险 API；二进制缺失则跳过）
        from src.engine.godot.gd_guard import scan_project

        guard = scan_project(project_path)
        if guard["available"] and guard["verdict"] == "block":
            return {
                "ok": False, "stage": "gd_guard",
                "errors": [
                    {"pattern": f.get("rule", ""), "snippet": f"{f.get('file','')}:{f.get('line','')} {f.get('detail','')} {f.get('snippet','')}"}
                    for f in guard["findings"][:5]
                ],
            }
        # 发布门禁 1：机械基线检查（确定性，零成本）
        from src.engine.godot.baseline_checker import check_project

        baseline = check_project(project_path)
        if not baseline["ok"]:
            return {
                "ok": False, "stage": "baseline",
                "errors": [
                    {"pattern": f["check"], "snippet": f["desc"] + (f" ({f['detail']})" if f["detail"] else "")}
                    for f in baseline["failures"]
                ],
            }
        # 门禁 1.5：headless 导入资源（新纹理/音效未 import 会导致运行时加载失败误报）
        from src.engine.godot.export_kit import ensure_imported

        ensure_imported(project_path, editor_path)
        # 发布门禁 2：真机跑 60 帧，有脚本错误/崩溃即拦下
        from src.engine.godot.runtime_smoke import GodotRuntimeSmoke

        smoke = GodotRuntimeSmoke({"godot": {
            "editor_path": editor_path, "project_path": project_path,
        }})
        smoke_result = smoke.run_scene(scene_path="res://scenes/main.tscn", frames=60)
        if not smoke_result.runnable:
            return {
                "ok": False, "stage": "release_gate",
                "errors": [
                    {"pattern": e.get("pattern", ""), "snippet": e.get("snippet", "")[:200]}
                    for e in (smoke_result.errors or [])[:5]
                ],
            }
        from src.engine.godot.export_kit import export_project

        return export_project(project_path, editor_path, preset_name=preset)

    result = await asyncio.to_thread(_gate_and_export)
    if not result.get("ok"):
        stage = result.get("stage", "export")
        is_gate = stage in {"release_gate", "baseline", "gd_guard"}
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "release_gate_failed" if is_gate else "export_failed",
                "message": ("发布门禁未通过（" + ("基线检查" if stage == "baseline" else "运行时冒烟") + "），游戏未能出包") if is_gate else "导出失败",
                "stage": stage,
                "errors": result.get("errors", []),
                "stderr_tail": (result.get("stderr_tail") or "")[-400:],
            },
        )

    out: Dict[str, Any] = {"ok": True, "preset": preset, "out_path": result["out_path"]}
    if preset == "Web":
        out["web_url"] = f"/play/{project_id}/index.html"
    return out


@app.get("/play/{project_id}/{file_path:path}")
async def serve_play(project_id: str, file_path: str):
    """伺服 Web 导出产物（带 COOP/COEP 头，Godot 线程导出必须）。"""
    project_path = _resolve_preview_project(project_id)
    web_root = os.path.abspath(os.path.join(project_path, "export", "web"))
    if not file_path or file_path in {".", "/"}:
        file_path = "index.html"
    full = os.path.abspath(os.path.join(web_root, file_path))
    if full != web_root and not full.startswith(web_root + os.sep):
        raise HTTPException(status_code=400, detail="非法路径")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="导出产物不存在（先调用导出接口）")

    ext = os.path.splitext(full)[1].lower()
    data = await asyncio.to_thread(lambda: Path(full).read_bytes())
    return Response(
        content=data,
        media_type=_PLAY_MIME.get(ext, "application/octet-stream"),
        headers={
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
            "Cache-Control": "no-store",
        },
    )


# ── Sandbox Management API ──

@app.post("/api/v1/sandbox/{project_id}/create")
async def sandbox_create(project_id: str, role: str = "director"):
    """创建沙箱任务工作区"""
    try:
        sandbox = SandboxController(config)
        task = sandbox.create(project_id, role=role)
        return {"ok": True, "task": task}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/sandbox/{project_id}/task/{task_id}/modify")
async def sandbox_modify(project_id: str, task_id: str, rel_path: str, content: str):
    """修改沙箱工作区文件"""
    try:
        sandbox = SandboxController(config)
        task_dir = os.path.join("data", "sandbox", project_id, "tasks", task_id)
        task = {"task_id": task_id, "task_dir": task_dir, "role": "director"}
        snap_id = sandbox.modify(task, rel_path, content)
        return {"ok": True, "snap_id": snap_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/sandbox/{project_id}/task/{task_id}/merge")
async def sandbox_merge(project_id: str, task_id: str):
    """合并沙箱工作区到主线"""
    try:
        sandbox = SandboxController(config)
        task_dir = os.path.join("data", "sandbox", project_id, "tasks", task_id)
        task = {"task_id": task_id, "task_dir": task_dir, "role": "director"}
        main_path = sandbox.merge(task)
        return {"ok": True, "merged_to": str(main_path)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/sandbox/{project_id}/task/{task_id}/rollback")
async def sandbox_rollback(project_id: str, task_id: str):
    """回滚沙箱工作区"""
    try:
        sandbox = SandboxController(config)
        task_dir = os.path.join("data", "sandbox", project_id, "tasks", task_id)
        task = {"task_id": task_id, "task_dir": task_dir, "role": "director"}
        sandbox.rollback(task)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/sandbox/{project_id}/status")
async def sandbox_status(project_id: str):
    """查询沙箱状态"""
    try:
        sandbox = SandboxController(config)
        return sandbox.status(project_id=project_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/v1/sandbox/{project_id}/task/{task_id}")
async def sandbox_destroy(project_id: str, task_id: str):
    """销毁沙箱任务工作区"""
    try:
        sandbox = SandboxController(config)
        sandbox.destroy(project_id, task_id=task_id)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/sandbox/{project_id}/cleanup")
async def sandbox_cleanup(project_id: str, keep_last: int = 5, max_age_hours: Optional[int] = 168):
    """清理旧沙箱任务，保留最近 N 个且未超龄的任务。"""
    try:
        sandbox = SandboxController(config)
        result = sandbox.cleanup(project_id, keep_last=keep_last, max_age_hours=max_age_hours)
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
