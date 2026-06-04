"""GameForge - 扩展API路由模块

只包含main.py中没有的独立路由：编译、导入、评测。
避免与主路由重复定义。
"""

import asyncio
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from src.api.schemas import (
    CompileRequest,
    CompileResponse,
    ImportRequest,
    ImportResponse,
    EvalRequest,
    EvalResponse,
)
from src.cli import load_config
from src.engine.unity import UnityEditor

router = APIRouter()


@router.post("/compile", response_model=CompileResponse)
async def compile_project(request: CompileRequest):
    """编译Unity项目（异步化阻塞操作）"""
    try:
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
