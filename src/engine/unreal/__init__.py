"""GameForge - Unreal引擎集成模块

提供Unreal Editor的编译、执行、导入等功能。
"""

import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class CompileResult:
    """编译结果"""
    success: bool
    errors: List[str]
    warnings: List[str]
    log_output: str = ""
    compile_time: float = 0.0


@dataclass
class ImportResult:
    """导入结果"""
    success: bool
    imported_files: List[str]
    failed_files: List[str]
    message: str = ""


class UnrealEditor:
    """Unreal编辑器接口"""

    def __init__(self, config: Dict[str, Any]):
        self.editor_path = config.get("unreal_editor_path", "")
        self.project_path = config.get("unreal_project_path", "")
        self.timeout = config.get("unreal_timeout", 600)

    def validate(self) -> Tuple[bool, str]:
        """验证配置"""
        if not self.editor_path:
            return False, "未配置Unreal Editor路径"
        if not os.path.exists(self.editor_path):
            return False, f"Unreal Editor不存在: {self.editor_path}"
        if not self.project_path:
            return False, "未配置Unreal项目路径"
        return True, ""

    def compile_project(self, log_file: Optional[str] = None) -> CompileResult:
        """编译Unreal项目"""
        is_valid, error = self.validate()
        if not is_valid:
            return CompileResult(success=False, errors=[error], warnings=[])

        if log_file is None:
            log_file = os.path.join(self.project_path, "Saved", "Logs", "compile.log")

        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        cmd = [
            self.editor_path,
            self.project_path,
            "-run=Compile",
            "-log",
            f"-abslog={log_file}",
            "-unattended",
            "-NoSplash",
            "-NoSound",
        ]

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.project_path,
            )
            compile_time = time.time() - start_time

            errors, warnings = self._parse_log(log_file)

            return CompileResult(
                success=result.returncode == 0,
                errors=errors,
                warnings=warnings,
                compile_time=compile_time,
            )
        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f"编译超时({self.timeout}秒)"],
                warnings=[],
                compile_time=self.timeout,
            )
        except Exception as e:
            return CompileResult(success=False, errors=[str(e)], warnings=[])

    def import_files(self, files: Dict[str, str]) -> ImportResult:
        """导入文件到Unreal项目"""
        is_valid, error = self.validate()
        if not is_valid:
            return ImportResult(success=False, imported_files=[], failed_files=[], message=error)

        imported = []
        failed = []

        for rel_path, content in files.items():
            if not rel_path.startswith("Source/"):
                rel_path = f"Source/{rel_path}"

            full_path = os.path.join(self.project_path, rel_path)
            try:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                imported.append(rel_path)
            except Exception as e:
                failed.append(f"{rel_path}: {str(e)}")

        return ImportResult(
            success=len(failed) == 0,
            imported_files=imported,
            failed_files=failed,
            message=f"导入{len(imported)}个文件，{len(failed)}个失败",
        )

    def _parse_log(self, log_file: str) -> Tuple[List[str], List[str]]:
        """解析编译日志"""
        errors = []
        warnings = []

        if not os.path.exists(log_file):
            return errors, warnings

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "error" in line.lower() and ("C++" in line or ".cpp" in line or ".h" in line):
                        errors.append(line.strip()[:200])
                    elif "warning" in line.lower() and ("C++" in line or ".cpp" in line or ".h" in line):
                        warnings.append(line.strip()[:200])
        except Exception:
            pass

        return errors[:50], warnings[:50]
