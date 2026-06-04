"""GameForge - 沙箱执行模块

提供安全的代码执行环境。
"""

import os
import subprocess
import tempfile
import shutil
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ExecutionResult:
    """代码执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    memory_used: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "execution_time": self.execution_time,
            "memory_used": self.memory_used,
        }


class SandboxExecutor:
    """沙箱执行器 - 提供隔离的代码执行环境"""

    def __init__(self, config: Dict[str, Any]):
        sandbox_config = config.get("sandbox", {})
        self.enabled = sandbox_config.get("enabled", True)
        self.timeout = sandbox_config.get("timeout", 300)
        self.memory_limit = sandbox_config.get("memory_limit", "512MB")
        self.allowed_paths = sandbox_config.get("allowed_paths", ["./projects", "./temp"])

    def execute_python(self, code: str, args: Optional[List[str]] = None) -> ExecutionResult:
        """在沙箱中执行Python代码

        Args:
            code: Python代码
            args: 命令行参数

        Returns:
            执行结果
        """
        if not self.enabled:
            return ExecutionResult(
                success=False, stdout="", stderr="Sandbox is disabled",
                exit_code=1, execution_time=0
            )

        temp_dir = tempfile.mkdtemp(prefix="gameforge_sandbox_")
        try:
            code_file = os.path.join(temp_dir, "script.py")
            with open(code_file, "w", encoding="utf-8") as f:
                f.write(code)

            cmd = ["python", code_file]
            if args:
                cmd.extend(args)

            start_time = datetime.now()
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=temp_dir,
            )
            end_time = datetime.now()

            execution_time = (end_time - start_time).total_seconds()

            return ExecutionResult(
                success=process.returncode == 0,
                stdout=process.stdout,
                stderr=process.stderr,
                exit_code=process.returncode,
                execution_time=execution_time,
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False, stdout="", stderr=f"Execution timed out after {self.timeout}s",
                exit_code=-1, execution_time=self.timeout
            )
        except Exception as e:
            return ExecutionResult(
                success=False, stdout="", stderr=str(e),
                exit_code=-1, execution_time=0
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def validate_code(self, code: str) -> Tuple[bool, List[str]]:
        """验证代码安全性

        Args:
            code: 代码内容

        Returns:
            (是否安全, 问题列表)
        """
        issues = []
        dangerous_imports = ["os.system", "subprocess.call", "eval(", "exec(", "__import__"]
        for pattern in dangerous_imports:
            if pattern in code:
                issues.append(f"检测到危险调用: {pattern}")

        dangerous_modules = ["shutil.rmtree", "os.remove", "os.unlink"]
        for pattern in dangerous_modules:
            if pattern in code:
                issues.append(f"检测到文件删除操作: {pattern}")

        return len(issues) == 0, issues

    def create_temp_project(self, files: Dict[str, str]) -> str:
        """创建临时项目目录

        Args:
            files: 文件字典 {相对路径: 内容}

        Returns:
            临时目录路径
        """
        temp_dir = tempfile.mkdtemp(prefix="gameforge_project_")

        for rel_path, content in files.items():
            file_path = os.path.join(temp_dir, rel_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        return temp_dir

    def cleanup_temp_project(self, temp_dir: str):
        """清理临时项目目录

        Args:
            temp_dir: 临时目录路径
        """
        if os.path.exists(temp_dir) and "gameforge" in temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
