"""GameForge - 扩展API路由模块

只包含main.py中没有的独立路由：编译、导入、评测。
专注于 Godot 引擎。
"""

import asyncio
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

import structlog

from src.api.schemas import (
    CompileRequest,
    CompileResponse,
    ImportRequest,
    ImportResponse,
    EvalRequest,
    EvalResponse,
)
from src.cli import load_config
from src.engine.godot import GodotEditor

logger = structlog.get_logger()

router = APIRouter()


@router.post("/compile", response_model=CompileResponse)
async def compile_project(request: CompileRequest):
    """编译 Godot 项目（异步化阻塞操作）"""
    try:
        config = load_config()
        godot_config = config.get("godot", {})
        if request.project_path:
            godot_config["project_path"] = request.project_path

        editor = GodotEditor(godot_config)
        is_valid, msg = editor.validate()
        if not is_valid:
            raise HTTPException(status_code=400, detail=msg)

        # 将阻塞的编译操作放到线程池执行
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, editor.compile_project)

        return CompileResponse(
            success=result.success,
            errors=[e.get("message", str(e)) for e in result.errors],
            warnings=[w.get("message", str(w)) for w in result.warnings],
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("compile_project_failed")
        raise HTTPException(status_code=500, detail="编译流程内部错误，请查看服务端日志")


@router.post("/import", response_model=ImportResponse)
async def import_files(request: ImportRequest):
    """导入文件到 Godot 项目（异步化阻塞操作）"""
    try:
        config = load_config()
        godot_config = config.get("godot", {})
        if request.project_path:
            godot_config["project_path"] = request.project_path

        editor = GodotEditor(godot_config)
        is_valid, msg = editor.validate()
        if not is_valid:
            raise HTTPException(status_code=400, detail=msg)

        # 将阻塞的导入操作放到线程池执行
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, editor.import_files, request.files
        )

        return ImportResponse(
            success=result.get("status") == "success",
            imported_files=result.get("imported", []),
            failed_files=result.get("errors", []),
            message="导入完成",
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("import_files_failed")
        raise HTTPException(status_code=500, detail="文件导入内部错误，请查看服务端日志")


@router.post("/eval", response_model=EvalResponse)
async def run_evaluation(request: EvalRequest):
    """运行代码评测"""
    try:
        from src.eval.metrics import run_evaluation as _run_evaluation

        report = _run_evaluation(request.project_name, code_files=request.code_files)

        return EvalResponse(
            project_name=report.project_name,
            overall_score=report.overall_score,
            metrics=[m.to_dict() for m in report.metrics],
        )
    except Exception:
        logger.exception("run_evaluation_failed")
        raise HTTPException(status_code=500, detail="代码评测内部错误，请查看服务端日志")
