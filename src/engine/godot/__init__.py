"""GameForge - Godot 引擎集成模块

提供 Godot 编辑器的 CLI/HTTP/WebSocket 交互能力。
支持 Godot 3.x 和 4.x 版本。
"""

import os
import json
import asyncio
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class GodotCompileResult:
    """Godot 编译结果"""
    success: bool
    errors: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]
    output: str
    godot_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "errors": self.errors,
            "warnings": self.warnings,
            "output": self.output,
            "godot_version": self.godot_version,
        }


class GodotCompiler:
    """Godot 编译器 — 通过 headless 模式验证 GDScript 语法"""

    def __init__(self, config: Dict[str, Any]):
        godot_config = config.get("godot", {})
        self.editor_path = godot_config.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        self.project_path = godot_config.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        self.timeout = godot_config.get("timeout", 300)

    def validate(self) -> Tuple[bool, str]:
        """验证 Godot 编辑器和项目路径"""
        if not self.editor_path:
            return False, "未配置 Godot 编辑器路径（设置 godot.editor_path 或 GODOT_EDITOR_PATH 环境变量）"
        if not os.path.isfile(self.editor_path):
            return False, f"Godot 编辑器不存在: {self.editor_path}"
        if not self.project_path:
            return False, "未配置 Godot 项目路径（设置 godot.project_path 或 GODOT_PROJECT_PATH 环境变量）"
        if not os.path.isdir(self.project_path):
            return False, f"Godot 项目目录不存在: {self.project_path}"
        project_file = os.path.join(self.project_path, "project.godot")
        if not os.path.isfile(project_file):
            return False, f"项目目录中未找到 project.godot: {self.project_path}"
        return True, "OK"

    def compile_project(self) -> GodotCompileResult:
        """使用 Godot headless 模式验证项目脚本

        使用 `godot --headless --script-res` 或 `godot --headless --check-only` 来验证 GDScript。
        """
        valid, msg = self.validate()
        if not valid:
            return GodotCompileResult(
                success=False, errors=[{"message": msg}], warnings=[], output=""
            )

        try:
            # Godot 4.x: --headless --check-only
            # Godot 3.x: --no-window --script (需要自定义验证脚本)
            cmd = [self.editor_path, "--headless", "--check-only", "--path", self.project_path]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                cwd=self.project_path,
            )

            errors = self._parse_errors(result.stderr)
            warnings = self._parse_warnings(result.stderr)

            return GodotCompileResult(
                success=result.returncode == 0 and not errors,
                errors=errors,
                warnings=warnings,
                output=result.stdout + result.stderr,
            )
        except subprocess.TimeoutExpired:
            return GodotCompileResult(
                success=False,
                errors=[{"message": f"编译超时（{self.timeout}秒）"}],
                warnings=[], output=""
            )
        except FileNotFoundError:
            return GodotCompileResult(
                success=False,
                errors=[{"message": f"找不到 Godot 编辑器: {self.editor_path}"}],
                warnings=[], output=""
            )
        except Exception as e:
            return GodotCompileResult(
                success=False,
                errors=[{"message": str(e)}],
                warnings=[], output=""
            )

    def _parse_errors(self, output: str) -> List[Dict[str, Any]]:
        """解析 Godot 错误输出"""
        errors = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Godot 4.x 错误格式: ERROR: message
            #   at: function (file:line)
            if line.startswith("ERROR:") or line.startswith("SCRIPT ERROR:"):
                errors.append({"message": line, "type": "error"})
            elif "error:" in line.lower() and (".gd:" in line or ".tscn:" in line):
                # 解析 file.gd:line: error: message 格式
                parts = line.split(":", 3)
                if len(parts) >= 3:
                    errors.append({
                        "file": parts[0].strip(),
                        "line": parts[1].strip(),
                        "message": parts[-1].strip(),
                        "type": "parse_error",
                    })
        return errors

    def _parse_warnings(self, output: str) -> List[Dict[str, Any]]:
        """解析 Godot 警告输出"""
        warnings = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("WARNING:") or "warning:" in line.lower():
                warnings.append({"message": line, "type": "warning"})
        return warnings


class GodotEditor:
    """Godot 编辑器交互类

    提供 CLI 模式（headless）和项目文件操作能力。
    """

    def __init__(self, config: Dict[str, Any]):
        godot_config = config.get("godot", {})
        self.editor_path = godot_config.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        self.project_path = godot_config.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        self.godot_version = godot_config.get("godot_version", 4)
        self.timeout = godot_config.get("timeout", 300)
        self.compiler = GodotCompiler(config)

    def validate(self) -> Tuple[bool, str]:
        """验证编辑器和项目配置"""
        return self.compiler.validate()

    def compile_project(self) -> GodotCompileResult:
        """编译/验证项目"""
        return self.compiler.compile_project()

    def import_files(self, files: Dict[str, str]) -> Dict[str, Any]:
        """将文件导入 Godot 项目

        Args:
            files: 文件字典 {相对路径: 内容}

        Returns:
            导入结果
        """
        if not self.project_path:
            return {"status": "error", "error": "未配置 Godot 项目路径"}

        imported = []
        errors = []

        for rel_path, content in files.items():
            try:
                file_path = os.path.join(self.project_path, rel_path)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                imported.append(rel_path)
            except Exception as e:
                errors.append(f"{rel_path}: {str(e)}")

        return {
            "status": "success" if not errors else "partial",
            "imported": imported,
            "errors": errors,
        }

    def execute_editor_script(self, script_path: str) -> Dict[str, Any]:
        """执行 Godot 编辑器脚本

        Args:
            script_path: 脚本路径（相对于项目根目录）

        Returns:
            执行结果
        """
        valid, msg = self.validate()
        if not valid:
            return {"status": "error", "error": msg}

        try:
            cmd = [self.editor_path, "--headless", "--script", script_path, "--path", self.project_path]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                cwd=self.project_path,
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def detect_version(self) -> Tuple[int, str]:
        """检测 Godot 版本

        Returns:
            (主版本号, 版本字符串)
        """
        if not self.editor_path or not os.path.isfile(self.editor_path):
            return self.godot_version, f"Godot {self.godot_version}.x (未验证)"

        try:
            result = subprocess.run(
                [self.editor_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            version_str = result.stdout.strip()
            major = int(version_str.split(".")[0]) if version_str else self.godot_version
            return major, version_str
        except Exception:
            return self.godot_version, f"Godot {self.godot_version}.x (检测失败)"
