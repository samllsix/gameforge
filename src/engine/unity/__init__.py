"""GameForge - Unity编辑器集成模块

提供Unity Editor的编译、执行、导入等直连功能。
"""

import os
import re
import subprocess
import time
import json
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompileResult:
    """Unity编译结果"""
    success: bool
    errors: List[str]
    warnings: List[str]
    log_output: str = ""
    compile_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "errors": self.errors,
            "warnings": self.warnings,
            "compile_time": self.compile_time,
        }


@dataclass
class ImportResult:
    """文件导入结果"""
    success: bool
    imported_files: List[str]
    failed_files: List[str]
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "imported_files": self.imported_files,
            "failed_files": self.failed_files,
            "message": self.message,
        }


class UnityEditor:
    """Unity编辑器接口 - 通过命令行与Unity Editor交互"""

    def __init__(self, config: Dict[str, Any]):
        """初始化Unity编辑器接口

        Args:
            config: 配置字典，需包含unity_editor_path和unity_project_path
        """
        self.editor_path = config.get("unity_editor_path", self._find_unity_editor())
        self.project_path = config.get("unity_project_path", "")
        self.timeout = config.get("unity_timeout", 300)

    def _find_unity_editor(self) -> str:
        """自动查找Unity Editor安装路径

        Returns:
            Unity.exe路径，未找到返回空字符串
        """
        search_paths = [
            "D:/Unity/2022.3.62f3c1/Editor/Unity.exe",
            "D:/Unity/2022.3.62t7/Editor/Unity.exe",
            "C:/Program Files/Unity/Hub/Editor/2022.3.62f1/Editor/Unity.exe",
            "C:/Program Files/Unity/Hub/Editor/2021.3.0f1/Editor/Unity.exe",
        ]

        for path in search_paths:
            if os.path.exists(path):
                return path

        # 尝试从注册表查找
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Unity Technologies\Unity Editor 5.x")
            # 读取最近打开的项目路径来推断Unity版本
            winreg.CloseKey(key)
        except Exception:
            pass

        return ""

    def validate(self) -> Tuple[bool, str]:
        """验证Unity Editor和项目路径

        Returns:
            (是否有效, 错误信息)
        """
        if not self.editor_path:
            return False, "未配置Unity Editor路径"

        if not os.path.exists(self.editor_path):
            return False, f"Unity Editor不存在: {self.editor_path}"

        if not self.project_path:
            return False, "未配置Unity项目路径"

        if not os.path.isdir(self.project_path):
            return False, f"Unity项目目录不存在: {self.project_path}"

        assets_dir = os.path.join(self.project_path, "Assets")
        if not os.path.isdir(assets_dir):
            return False, f"无效的Unity项目: 缺少Assets目录"

        return True, ""

    def compile_project(self, log_file: Optional[str] = None) -> CompileResult:
        """编译Unity项目

        通过Unity batch模式编译项目，检查脚本错误。

        Args:
            log_file: 可选的日志文件路径

        Returns:
            编译结果
        """
        is_valid, error = self.validate()
        if not is_valid:
            return CompileResult(success=False, errors=[error], warnings=[])

        if log_file is None:
            log_file = os.path.join(self.project_path, "Logs", "compile_log.txt")

        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        cmd = [
            self.editor_path,
            "-batchmode",
            "-nographics",
            "-projectPath", self.project_path,
            "-executeMethod", "GameForge.Editor.CompilationHelper.CompileAndCheck",
            "-logFile", log_file,
            "-quit",
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

            log_content = ""
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    log_content = f.read()

            errors, warnings = self._parse_compile_log(log_content)

            # 检查退出码
            if result.returncode != 0 and not errors:
                errors.append(f"Unity进程退出码: {result.returncode}")
                if result.stderr:
                    errors.append(result.stderr[:500])

            return CompileResult(
                success=result.returncode == 0 and len(errors) == 0,
                errors=errors,
                warnings=warnings,
                log_output=log_content[:5000],
                compile_time=compile_time,
            )

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f"编译超时({self.timeout}秒)"],
                warnings=[],
                compile_time=self.timeout,
            )
        except FileNotFoundError:
            return CompileResult(
                success=False,
                errors=[f"找不到Unity Editor: {self.editor_path}"],
                warnings=[],
            )
        except Exception as e:
            return CompileResult(
                success=False,
                errors=[str(e)],
                warnings=[],
            )

    def import_files(self, files: Dict[str, str]) -> ImportResult:
        """将文件导入Unity项目

        直接写入文件到Assets目录，Unity会自动检测变更。

        Args:
            files: 文件字典 {相对路径: 内容}

        Returns:
            导入结果
        """
        is_valid, error = self.validate()
        if not is_valid:
            return ImportResult(success=False, imported_files=[], failed_files=[], message=error)

        imported = []
        failed = []

        for rel_path, content in files.items():
            # 确保路径在Assets下
            if not rel_path.startswith("Assets/"):
                rel_path = f"Assets/Scripts/{rel_path}"

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

    def execute_editor_script(self, script_class: str, method: str = "Execute", args: Optional[List[str]] = None) -> CompileResult:
        """执行Unity Editor脚本

        Args:
            script_class: 脚本类的完整名称（命名空间.类名）
            method: 要执行的方法名
            args: 命令行参数

        Returns:
            执行结果
        """
        is_valid, error = self.validate()
        if not is_valid:
            return CompileResult(success=False, errors=[error], warnings=[])

        execute_method = f"{script_class}.{method}"

        cmd = [
            self.editor_path,
            "-batchmode",
            "-nographics",
            "-projectPath", self.project_path,
            "-executeMethod", execute_method,
            "-quit",
        ]

        if args:
            cmd.extend(args)

        log_file = os.path.join(self.project_path, "Logs", "editor_script_log.txt")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        cmd.extend(["-logFile", log_file])

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

            log_content = ""
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    log_content = f.read()

            errors, warnings = self._parse_compile_log(log_content)

            if result.returncode != 0 and not errors:
                errors.append(f"脚本执行失败，退出码: {result.returncode}")

            return CompileResult(
                success=result.returncode == 0,
                errors=errors,
                warnings=warnings,
                log_output=log_content[:5000],
                compile_time=compile_time,
            )

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f"脚本执行超时({self.timeout}秒)"],
                warnings=[],
                compile_time=self.timeout,
            )
        except Exception as e:
            return CompileResult(
                success=False,
                errors=[str(e)],
                warnings=[],
            )

    def refresh_assets(self) -> bool:
        """刷新Unity资源数据库

        Returns:
            是否成功
        """
        result = self.execute_editor_script("UnityEditor.AssetDatabase", "Refresh")
        return result.success

    def get_compile_errors(self) -> List[str]:
        """获取当前项目的编译错误

        通过读取Unity的Editor.log来获取编译错误。

        Returns:
            错误列表
        """
        editor_log_paths = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Unity", "Editor", "Editor.log"),
            os.path.join(self.project_path, "Logs", "Editor.log"),
        ]

        errors = []
        for log_path in editor_log_paths:
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    # 匹配C#编译错误
                    error_pattern = r'(\w+\.cs)\((\d+),(\d+)\):\s*error\s+(\w+):\s*(.+)'
                    for match in re.finditer(error_pattern, content):
                        file_name, line, col, code, message = match.groups()
                        errors.append(f"{file_name}({line},{col}): {code}: {message}")

                    break
                except Exception:
                    continue

        return errors

    def _parse_compile_log(self, log_content: str) -> Tuple[List[str], List[str]]:
        """解析Unity编译日志

        Args:
            log_content: 日志内容

        Returns:
            (错误列表, 警告列表)
        """
        errors = []
        warnings = []

        # 忽略的非错误模式
        ignore_patterns = [
            'Callback aborted',
            'MemoryLeaks',
            'StackAllocator',
            'utp:',
            'ALLOC_',
        ]

        # 匹配C#编译错误 (CS开头的错误码)
        error_pattern = r'(?:Assets[/\\].*\.cs|error)\s*(?:\(.*\))?\s*:\s*error\s+CS\d+:.*'

        for match in re.finditer(error_pattern, log_content, re.IGNORECASE):
            error_text = match.group(0).strip()
            if not any(ignore in error_text for ignore in ignore_patterns):
                if error_text not in errors:
                    errors.append(error_text[:200])

        # 匹配CompilationFailedException
        if 'CompilationFailedException' in log_content:
            errors.append('CompilationFailedException: 编译失败')

        # 匹配警告
        warning_pattern = r'warning\s+CS\d+:.*'
        for match in re.finditer(warning_pattern, log_content, re.IGNORECASE):
            warning_text = match.group(0).strip()
            if warning_text not in warnings:
                warnings.append(warning_text[:200])

        return errors[:50], warnings[:50]


def create_editor_script(script_name: str, methods: List[Dict[str, str]]) -> str:
    """生成Unity Editor脚本

    Args:
        script_name: 脚本名称
        methods: 方法列表，每个包含name和body

    Returns:
        C#脚本内容
    """
    method_code = ""
    for method in methods:
        method_code += f"""
    [MenuItem("GameForge/{method.get('name', 'Execute')}")]
    public static void {method.get('name', 'Execute')}()
    {{
{method.get('body', '        // TODO: implement')}
    }}
"""

    return f'''using UnityEngine;
using UnityEditor;

namespace GameForge.Editor
{{
    /// <summary>
    /// {script_name} - 自动生成的Editor脚本
    /// </summary>
    public static class {script_name}
    {{
{method_code}
    }}
}}'''


def create_compile_checker_script() -> str:
    """生成编译检查器脚本

    Returns:
        CompilationHelper.cs脚本内容
    """
    return '''using UnityEngine;
using UnityEditor;
using UnityEditor.Compilation;
using System.IO;

namespace GameForge.Editor
{
    /// <summary>
    /// 编译检查器 - 用于batch模式下的编译验证
    /// </summary>
    public static class CompilationHelper
    {
        private static string logPath = "Logs/compile_result.json";

        [MenuItem("GameForge/Compile And Check")]
        public static void CompileAndCheck()
        {
            Debug.Log("[GameForge] Starting compile check...");

            // 等待编译完成
            CompilationPipeline.compilationFinished += OnCompilationFinished;

            // 触发编译
            CompilationPipeline.RequestScriptCompilation();

            // 等待一下让编译开始
            System.Threading.Thread.Sleep(1000);
        }

        private static void OnCompilationFinished(object obj)
        {
            CompilationPipeline.compilationFinished -= OnCompilationFinished;

            var messages = CompilationPipeline.GetAssemblies();
            bool hasErrors = false;

            // 检查编译消息
            foreach (var assembly in messages)
            {
                foreach (var message in assembly.compiledAssembly.compilerMessages)
                {
                    if (message.type == CompilerMessageType.Error)
                    {
                        Debug.LogError($"[GameForge] Compile Error: {message.message}");
                        hasErrors = true;
                    }
                    else if (message.type == CompilerMessageType.Warning)
                    {
                        Debug.LogWarning($"[GameForge] Compile Warning: {message.message}");
                    }
                }
            }

            // 写入结果
            var result = new
            {
                success = !hasErrors,
                timestamp = System.DateTime.Now.ToString("o"),
            };

            string json = JsonUtility.ToJson(result, true);
            File.WriteAllText(logPath, json);

            Debug.Log($"[GameForge] Compile check {(hasErrors ? "FAILED" : "PASSED")}");

            // batch模式下退出
            if (Application.isBatchMode)
            {
                EditorApplication.Exit(hasErrors ? 1 : 0);
            }
        }
    }
}'''
