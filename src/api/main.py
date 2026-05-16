"""GameForge - API服务器模块

提供RESTful API接口。
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import uvicorn

from src.core.graph.workflow import create_workflow


# 创建FastAPI应用
app = FastAPI(
    title="GameForge API",
    description="游戏研发全流程AI Agent协作平台",
    version="0.1.0",
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 请求/响应模型
class GenerateRequest(BaseModel):
    """代码生成请求"""
    requirements: str
    engine: str = "unity"
    project_name: str = "GameForge Project"


class GenerateResponse(BaseModel):
    """代码生成响应"""
    success: bool
    code_generated: Dict[str, str]
    task_count: int
    fix_count: int


class TaskPlanRequest(BaseModel):
    """任务规划请求"""
    requirements: str


class TaskPlanResponse(BaseModel):
    """任务规划响应"""
    tasks: List[Dict[str, Any]]


# 全局配置
config = {
    "app": {
        "name": "GameForge",
        "version": "0.1.0",
        "environment": "development",
    },
    "llm": {
        "default_model": "claude-3-5-sonnet-20241022",
    },
    "agents": {
        "orchestrator": {"max_iterations": 10},
        "planner": {"max_tasks": 20},
        "code_generator": {"supported_engines": ["unity", "unreal"]},
        "debugger": {"max_fix_attempts": 5},
    },
}


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "GameForge API",
        "version": "0.1.0",
        "description": "游戏研发全流程AI Agent协作平台",
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


@app.post("/api/v1/generate", response_model=GenerateResponse)
async def generate_code(request: GenerateRequest):
    """生成游戏代码

    Args:
        request: 生成请求

    Returns:
        生成结果
    """
    try:
        # 创建工作流
        workflow = create_workflow(config)

        # 运行工作流
        result = await workflow.run({
            "project_context": {
                "engine": request.engine,
                "project_name": request.project_name,
            },
            "requirements": request.requirements,
        })

        return GenerateResponse(
            success=True,
            code_generated=result.get("code_generated", {}),
            task_count=len(result.get("task_plan", [])),
            fix_count=len(result.get("fix_history", [])),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/plan", response_model=TaskPlanResponse)
async def plan_tasks(request: TaskPlanRequest):
    """规划任务

    Args:
        request: 规划请求

    Returns:
        任务计划
    """
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

        return TaskPlanResponse(tasks=tasks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


def start_server(host: str = "0.0.0.0", port: int = 8000):
    """启动服务器

    Args:
        host: 主机地址
        port: 端口号
    """
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()
