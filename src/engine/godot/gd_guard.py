"""GameForge - gd-guard 安全闸门 Python 接线

gd-guard 是 Rust 编写的静态安全扫描器(tools/gd-guard/),
对 LLM 生成的 .gd 脚本、.tscn 场景、project.godot 做危险 API 黑名单
扫描与结构校验(执行/文件/网络/联机类 API 一票否决)。

本模块负责: 定位二进制、调用扫描、解析 JSON 判定。
二进制缺失时返回 available=False, 调用方按"跳过该层、不拦"处理(优雅降级)。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()

_GUARD_NAME = "gd-guard.exe"
_repo_root = Path(__file__).resolve().parents[3]


def find_guard() -> Optional[str]:
    """定位 gd-guard 二进制: 仓库构建产物 → PATH。找不到返回 None。"""
    candidates = [
        _repo_root / "tools" / "gd-guard" / "target" / "release" / _GUARD_NAME,
        _repo_root / "tools" / "gd-guard" / "target" / "debug" / _GUARD_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    from shutil import which

    return which(_GUARD_NAME)


def scan_project(project_path: str, timeout: float = 120.0) -> Dict[str, Any]:
    """扫描整个生成项目。

    返回:
        {
          "available": bool,      # 闸门是否可用(二进制存在)
          "verdict": "allow"|"block"|"unavailable",
          "findings": [...],      # 每项 {file,line,rule,detail,snippet}
          "scanned": {...},
        }
    闸门自身崩溃/超时按 available=False 处理(失败开放, 由其它门禁兜底)。
    """
    guard = find_guard()
    if not guard:
        return {"available": False, "verdict": "unavailable", "findings": [], "scanned": {}}
    try:
        proc = subprocess.run(
            [guard, "scan", str(project_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        body = json.loads(proc.stdout or "{}")
        verdict = "block" if proc.returncode == 1 else "allow"
        if proc.returncode not in (0, 1):
            # 用法/IO 错误: 不当判定, 视为不可用
            logger.warning("gd_guard.error_exit", code=proc.returncode, stderr=(proc.stderr or "")[-200:])
            return {"available": False, "verdict": "unavailable", "findings": [], "scanned": {}}
        result = {
            "available": True,
            "verdict": verdict,
            "findings": body.get("findings", []),
            "scanned": body.get("scanned", {}),
        }
        if verdict == "block":
            logger.warning("gd_guard.blocked", findings=result["findings"][:3])
        else:
            logger.info("gd_guard.allow", scanned=result["scanned"])
        return result
    except subprocess.TimeoutExpired:
        logger.warning("gd_guard.timeout", timeout=timeout)
        return {"available": False, "verdict": "unavailable", "findings": [], "scanned": {}}
    except Exception as e:  # noqa: BLE001
        logger.warning("gd_guard.failed", error=str(e))
        return {"available": False, "verdict": "unavailable", "findings": [], "scanned": {}}
