"""GameForge - Unreal引擎集成模块

提供Unreal Editor的编译、执行、导入等功能。
"""

import os
import re
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
        self._project_name = ""

    def _find_unreal_editor(self) -> str:
        """自动发现Unreal Engine安装"""
        search_paths = [
            "C:/Program Files/Epic Games/UE_5.4/Engine/Binaries/Win64/UnrealEditor.exe",
            "C:/Program Files/Epic Games/UE_5.3/Engine/Binaries/Win64/UnrealEditor.exe",
            "C:/Program Files/Epic Games/UE_5.2/Engine/Binaries/Win64/UnrealEditor.exe",
            "D:/Epic Games/UE_5.4/Engine/Binaries/Win64/UnrealEditor.exe",
            "D:/Epic Games/UE_5.3/Engine/Binaries/Win64/UnrealEditor.exe",
        ]
        for path in search_paths:
            if os.path.exists(path):
                return path
        return ""

    def _get_project_name(self) -> str:
        """获取项目名称（从.uproject文件名）"""
        if self._project_name:
            return self._project_name
        if self.project_path and os.path.isdir(self.project_path):
            for f in os.listdir(self.project_path):
                if f.endswith(".uproject"):
                    self._project_name = f.replace(".uproject", "")
                    return self._project_name
        return ""

    def validate(self) -> Tuple[bool, str]:
        """验证配置"""
        if not self.editor_path:
            self.editor_path = self._find_unreal_editor()
            if not self.editor_path:
                return False, "未配置Unreal Editor路径且未找到已安装的Unreal Engine"
        if not os.path.exists(self.editor_path):
            return False, f"Unreal Editor不存在: {self.editor_path}"
        if not self.project_path:
            return False, "未配置Unreal项目路径"
        if not os.path.isdir(self.project_path):
            return False, f"Unreal项目目录不存在: {self.project_path}"
        # 检查.uproject文件
        uproject_files = [f for f in os.listdir(self.project_path) if f.endswith(".uproject")]
        if not uproject_files:
            return False, f"无效的Unreal项目: 缺少.uproject文件"
        return True, ""

    def compile_project(self, log_file: Optional[str] = None) -> CompileResult:
        """编译Unreal项目"""
        is_valid, error = self.validate()
        if not is_valid:
            return CompileResult(success=False, errors=[error], warnings=[])

        if log_file is None:
            log_file = os.path.join(self.project_path, "Saved", "Logs", "compile.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        # 尝试使用UnrealBuildTool
        engine_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(self.editor_path))))
        ubt_path = os.path.join(
            engine_root, "Engine", "Binaries", "DotNET", "UnrealBuildTool", "UnrealBuildTool.exe"
        )

        if os.path.exists(ubt_path):
            cmd = [
                ubt_path,
                self._get_project_name(),
                "Win64", "Development",
                f"-Project={self.project_path}",
                "-WaitMutex",
                f"-log={log_file}",
            ]
        else:
            # 回退到Editor Build命令
            cmd = [
                self.editor_path,
                self.project_path,
                "-run=world",
                "-log",
                f"-abslog={log_file}",
                "-unattended",
                "-NoSplash",
                "-NoSound",
                "-build",
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
        """解析Unreal编译日志（支持MSVC错误码、LNK错误、C++错误）"""
        errors = []
        warnings = []

        if not os.path.exists(log_file):
            return errors, warnings

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line_stripped = line.strip()
                    # MSVC错误码模式 (e.g., error C2065, error LNK2019)
                    msvc_match = re.search(r'((?:error|fatal error)\s+[A-Z]+\d+)', line_stripped, re.IGNORECASE)
                    if msvc_match:
                        if line_stripped not in errors:
                            errors.append(line_stripped[:200])
                        continue
                    # 通用C++错误
                    if "error" in line_stripped.lower() and any(
                        ext in line_stripped for ext in [".cpp", ".h", "C++", "MSVC", "LNK", "LINK"]
                    ):
                        if line_stripped not in errors:
                            errors.append(line_stripped[:200])
                    elif "warning" in line_stripped.lower() and any(
                        ext in line_stripped for ext in [".cpp", ".h", "C++", "MSVC"]
                    ):
                        if line_stripped not in warnings:
                            warnings.append(line_stripped[:200])
        except Exception:
            pass

        return errors[:50], warnings[:50]
