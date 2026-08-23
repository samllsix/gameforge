"""GameForge - Godot 引擎集成模块

提供 Godot 编辑器的 CLI/HTTP/WebSocket 交互能力。
支持 Godot 3.x 和 4.x 版本。
"""

import os
import json
import asyncio
import subprocess
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

_ENV_TMPL = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _resolve_env(value: str) -> str:
    """展开 ``${VAR}`` / ``${VAR:default}`` 形式的环境变量模板。

    配置加载器（yaml.safe_load）不会解析这类模板，而 config.yaml 中的
    ``godot.editor_path: ${GODOT_EDITOR_PATH:}`` 会原样保留为字面量字符串。
    若不展开，``GodotEditor`` 会拿到字面量 ``${GODOT_EDITOR_PATH:}``（truthy），
    导致 ``or os.getenv(...)`` 兜底永不触发、``validate()`` 误判引擎缺失。
    """
    if not value or "${" not in value:
        return value

    def _sub(m: "re.Match") -> str:
        name = m.group(1)
        default = m.group(2) if m.group(2) is not None else ""
        return os.getenv(name, default)

    return _ENV_TMPL.sub(_sub, value)


def _normalize_godot_path(p: str) -> str:
    """把 Git Bash 风格的 /d/godot/... 归一化为 Windows 的 D:/godot/...

    在 Windows 上 Python 的 os.path 不会翻译 /d/ 前缀，直接当成相对路径，
    导致 isfile 失败、headless 路径无法识别。
    """
    if not p:
        return p
    if (
        p.startswith("/")
        and len(p) > 2
        and p[1].isalpha()
        and p[2] == "/"
    ):
        return p[1].upper() + ":" + p[2:]
    return p


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
        self.editor_path = _normalize_godot_path(_resolve_env(
            godot_config.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        ))
        self.project_path = _normalize_godot_path(_resolve_env(
            godot_config.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        ))
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
        self.editor_path = _normalize_godot_path(_resolve_env(
            godot_config.get("editor_path", "") or os.getenv("GODOT_EDITOR_PATH", "")
        ))
        self.project_path = _normalize_godot_path(_resolve_env(
            godot_config.get("project_path", "") or os.getenv("GODOT_PROJECT_PATH", "")
        ))
        self.godot_version = godot_config.get("godot_version", 4)
        self.timeout = godot_config.get("timeout", 300)
        self.compiler = GodotCompiler(config)

    def validate(self) -> Tuple[bool, str]:
        """验证编辑器和项目配置"""
        return self.compiler.validate()

    def compile_project(self) -> GodotCompileResult:
        """编译/验证项目"""
        return self.compiler.compile_project()

    def check_scripts(self, script_paths: List[str]) -> GodotCompileResult:
        """Headless 语法校验指定 GDScript 文件（无需打开编辑器 GUI）

        通过 ``godot --headless --script res://addons/gameforge/syntax_check.gd``
        触发 Godot 逐个解析目标脚本，从 stderr 抓取 ``SCRIPT ERROR`` /
        ``Failed to load script`` 行。不会运行项目主场景。

        Args:
            script_paths: res:// 形式的脚本路径列表，如 ["res://scripts/player.gd"]

        Returns:
            GodotCompileResult
        """
        valid, msg = self.validate()
        if not valid:
            return GodotCompileResult(
                success=False, errors=[{"message": msg}], warnings=[], output=""
            )
        if not script_paths:
            return GodotCompileResult(success=True, errors=[], warnings=[], output="")

        # 写 manifest（Python 侧绝对路径 -> res:// 映射由脚本自行处理）
        manifest = {"scripts": [p for p in script_paths if p]}
        manifest_path = os.path.join(self.project_path, "_gf_check_manifest.json")
        try:
            if os.path.exists(manifest_path):
                os.chmod(manifest_path, 0o666)
                os.remove(manifest_path)
        except Exception:
            pass
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
        except PermissionError:
            import tempfile
            fd, tmp_path = tempfile.mkstemp(
                dir=self.project_path, suffix=".json", prefix="_gf_check_manifest_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, ensure_ascii=False)
                manifest_path = tmp_path
            except Exception as e:
                return GodotCompileResult(
                    success=False,
                    errors=[{"message": f"写入校验 manifest 失败: {e}"}],
                    warnings=[], output="",
                )
        except Exception as e:
            return GodotCompileResult(
                success=False,
                errors=[{"message": f"写入校验 manifest 失败: {e}"}],
                warnings=[], output="",
            )

        check_script = "res://addons/gameforge/syntax_check.gd"
        cmd = [
            self.editor_path, "--headless", "--script", check_script,
            "--path", self.project_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                cwd=self.project_path,
            )
            output = result.stdout + result.stderr
            errors = self._parse_headless_errors(output)
            # 清理临时文件
            try:
                os.remove(manifest_path)
                res_path = os.path.join(self.project_path, "_gf_check_result.json")
                if os.path.isfile(res_path):
                    os.remove(res_path)
            except Exception:
                pass
            return GodotCompileResult(
                success=len(errors) == 0,
                errors=errors,
                warnings=self.compiler._parse_warnings(output),
                output=output,
            )
        except subprocess.TimeoutExpired:
            return GodotCompileResult(
                success=False,
                errors=[{"message": f"语法校验超时（{self.timeout}秒）"}],
                warnings=[], output="",
            )
        except FileNotFoundError:
            return GodotCompileResult(
                success=False,
                errors=[{"message": f"找不到 Godot 引擎: {self.editor_path}"}],
                warnings=[], output="",
            )
        except Exception as e:
            return GodotCompileResult(
                success=False, errors=[{"message": str(e)}], warnings=[], output="",
            )

    def _parse_headless_errors(self, output: str) -> List[Dict[str, Any]]:
        """解析 headless 校验输出中的脚本错误

        典型输出：
            SCRIPT ERROR: Parse Error: Expected expression for variable initial value after "=".
            ERROR: Failed to load script "res://scripts/foo.gd" with error "Parse error".
        """
        errors: List[Dict[str, Any]] = []
        last_script_msg = ""
        for line in output.splitlines():
            line = line.rstrip()
            stripped = line.strip()
            if stripped.startswith("SCRIPT ERROR:"):
                last_script_msg = stripped[len("SCRIPT ERROR:"):].strip()
                continue
            if "Failed to load script" in stripped:
                # 提取被引号包裹的 res:// 路径
                import re
                m = re.search(r'["\'](res://[^"\']+)["\']', stripped)
                file_path = m.group(1) if m else ""
                msg = last_script_msg or "Parse error"
                errors.append({
                    "file": file_path,
                    "line": "",
                    "message": msg,
                    "type": "error",
                })
                last_script_msg = ""
        return errors

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
                clean = rel_path.removeprefix("res://").removeprefix("res:/")
                file_path = os.path.join(self.project_path, clean)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                imported.append(clean)
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

    def render_screenshot_frame(
        self,
        project_path: str,
        scene_path: str = "res://scenes/main.tscn",
        output_path: Optional[str] = None,
        width: int = 640,
        height: int = 360,
        warmup_frames: int = 12,
        frame_index: int = 0,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """用 Godot headless 渲染一个场景并保存为 PNG。

        实现：通过 ``godot --headless --script res://addons/gameforge/screenshot_scene.gd`` 启动
        Godot 一次性脚本，脚本会读取项目根的 _gf_screenshot_manifest.json，加载目标场景、推进
        warmup_frames 帧、最后把 SubViewport 内容写入 output_path。

        Args:
            project_path: 项目根目录（绝对路径，含 project.godot）
            scene_path: res:// 形式的场景文件路径
            output_path: PNG 输出绝对路径；默认写到项目根的 _gf_screenshot_output.png
            width: 渲染宽度
            height: 渲染高度
            warmup_frames: 预热帧数（让 _ready / _process 跑出稳定画面）
            frame_index: 透传给脚本的帧序号（用于按帧区分序列截图）
            timeout: 子进程超时秒数

        Returns:
            {"ok": True, "output_path": "..."} 或 {"ok": False, "error": "..."}
        """
        if not self.editor_path or not os.path.isfile(self.editor_path):
            return {"ok": False, "error": f"Godot 编辑器未配置或不存在: {self.editor_path}"}
        if not project_path or not os.path.isdir(project_path):
            return {"ok": False, "error": f"项目目录不存在: {project_path}"}
        if not os.path.isfile(os.path.join(project_path, "project.godot")):
            return {"ok": False, "error": f"项目目录中找不到 project.godot: {project_path}"}
        if not scene_path:
            return {"ok": False, "error": "scene_path 不能为空"}

        if not output_path:
            output_path = os.path.join(project_path, "_gf_screenshot_output.png")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        manifest = {
            "scene_path": scene_path,
            "output_path": output_path,
            "width": int(width),
            "height": int(height),
            "warmup_frames": int(warmup_frames),
            "frame_index": int(frame_index),
        }
        manifest_path = os.path.join(project_path, "_gf_screenshot_manifest.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
        except Exception as e:
            return {"ok": False, "error": f"写入 manifest 失败: {e}"}

        # 把一次性截图脚本拷贝到目标项目的 addons/gameforge/ 下。
        # 由于每个项目独立，插件 addons 不一定已挂载到当前工程；脚本用 res:// 寻址
        # 时必须存在。
        script_rel = "addons/gameforge/screenshot_scene.gd"
        script_abs = os.path.join(project_path, script_rel.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(script_abs), exist_ok=True)
            # 优先用源 addons 中的权威脚本（仓库根 = src/engine/godot/__init__.py 向上 4 层）
            source_script = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "addons", "gameforge", "screenshot_scene.gd",
            )
            if os.path.isfile(source_script):
                with open(source_script, "r", encoding="utf-8") as sf, \
                        open(script_abs, "w", encoding="utf-8") as df:
                    df.write(sf.read())
            elif not os.path.isfile(script_abs):
                return {"ok": False, "error": "找不到 screenshot_scene.gd 源脚本"}
        except Exception as e:
            return {"ok": False, "error": f"准备截图脚本失败: {e}"}

        script_path = "res://" + script_rel
        cmd = [
            self.editor_path, "--headless", "--script", script_path,
            "--path", project_path,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                cwd=project_path,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Godot 渲染超时（{timeout}s）"}
        except FileNotFoundError:
            return {"ok": False, "error": f"找不到 Godot 引擎: {self.editor_path}"}
        except Exception as e:
            return {"ok": False, "error": f"调用 Godot 失败: {e}"}

        if not os.path.isfile(output_path):
            return {
                "ok": False,
                "error": "Godot 退出但未生成截图",
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
            }
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": f"Godot 退出码非零: {proc.returncode}",
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        return {
            "ok": True,
            "output_path": output_path,
            "width": width,
            "height": height,
            "frame_index": frame_index,
            "stdout": proc.stdout,
        }


# 重新导出 Supervisor（避免循环导入）
from src.engine.godot.supervisor import (
    GodotSupervisor,
    GodotTimeout,
    GodotCrashed,
    supervisor_lifespan,
)  # noqa: E402
