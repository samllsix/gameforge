"""GameForge - API路由模块

定义FastAPI路由，按功能分组。所有阻塞操作使用run_in_executor异步化。
"""

import asyncio
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from src.api.schemas import (
    GenerateRequest,
    GenerateResponse,
    TaskPlanRequest,
    TaskPlanResponse,
    CompileRequest,
    CompileResponse,
    ImportRequest,
    ImportResponse,
    EvalRequest,
    EvalResponse,
    HealthResponse,
    AgentListResponse,
    AgentInfo,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    return HealthResponse()


@router.get("/agents", response_model=AgentListResponse)
async def list_agents():
    """列出所有可用的Agent"""
    agents = [
        AgentInfo(name="orchestrator", description="编排Agent - 任务调度和流程控制"),
        AgentInfo(name="planner", description="规划Agent - 解析需求并生成任务计划"),
        AgentInfo(name="code_generator", description="代码生成Agent - 生成游戏代码"),
        AgentInfo(name="code_reviewer", description="代码审查Agent - 审查代码质量"),
        AgentInfo(name="test_generator", description="测试生成Agent - 生成测试用例"),
        AgentInfo(name="debugger", description="调试Agent - 分析错误并生成修复方案"),
        AgentInfo(name="refactor", description="重构Agent - 分析代码质量并优化重构"),
    ]
    return AgentListResponse(agents=agents)


@router.post("/generate", response_model=GenerateResponse)
async def generate_code(request: GenerateRequest):
    """生成游戏代码"""
    try:
        from src.core.graph.workflow import create_workflow
        from src.cli import load_config

        config = load_config()
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


@router.post("/plan", response_model=TaskPlanResponse)
async def plan_tasks(request: TaskPlanRequest):
    """规划任务"""
    try:
        from src.agents.planner import PlannerAgent
        from src.cli import load_config

        config = load_config()
        planner = PlannerAgent(config)

        state = {
            "project_context": {"requirements": request.requirements, "engine": "unity"},
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
        return TaskPlanResponse(tasks=tasks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compile", response_model=CompileResponse)
async def compile_project(request: CompileRequest):
    """编译Unity项目（异步化阻塞操作）"""
    try:
        from src.engine.unity import UnityEditor
        from src.cli import load_config

        config = load_config()
        unity_config = config.get("unity", {})
        if request.project_path:
            unity_config["unity_project_path"] = request.project_path

        editor = UnityEditor(unity_config)
        is_valid, msg = editor.validate()
        if not is_valid:
            raise HTTPException(status_code=400, detail=msg)

        # 将阻塞的编译操作放到线程池执行
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, editor.compile_project)

        return CompileResponse(
            success=result.success,
            errors=result.errors,
            warnings=result.warnings,
            compile_time=result.compile_time,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import", response_model=ImportResponse)
async def import_files(request: ImportRequest):
    """导入文件到Unity项目（异步化阻塞操作）"""
    try:
        from src.engine.unity import UnityEditor
        from src.cli import load_config

        config = load_config()
        unity_config = config.get("unity", {})
        if request.project_path:
            unity_config["unity_project_path"] = request.project_path

        editor = UnityEditor(unity_config)
        is_valid, msg = editor.validate()
        if not is_valid:
            raise HTTPException(status_code=400, detail=msg)

        # 将阻塞的导入操作放到线程池执行
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, editor.import_files, request.files
        )

        return ImportResponse(
            success=result.success,
            imported_files=result.imported_files,
            failed_files=result.failed_files,
            message=result.message,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/eval", response_model=EvalResponse)
async def run_evaluation(request: EvalRequest):
    """运行代码评测"""
    try:
        from src.eval.metrics import run_evaluation

        report = run_evaluation(request.project_name, code_files=request.code_files)

        return EvalResponse(
            project_name=report.project_name,
            overall_score=report.overall_score,
            metrics=[m.to_dict() for m in report.metrics],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
