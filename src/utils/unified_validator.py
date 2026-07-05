"""GameForge - 统一验证入口

整合验证器，提供统一调用接口，避免重复检查。
检查顺序：GDScript语法 → Godot兼容性 → 一致性，每层只检查上一层未覆盖的项。

专注于 Godot GDScript 代码验证。
"""

from typing import Any, Dict, List
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger()


@dataclass
class UnifiedValidationResult:
    """统一验证结果"""
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_issues(self) -> bool:
        return bool(self.errors or self.warnings or self.suggestions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": not self.has_errors,
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


def validate_all(
    code_files: Dict[str, str],
    scene_desc: Dict[str, Any] = None,
    gdm: Dict[str, Any] = None,
    file_metadata: Dict[str, Any] = None,
) -> UnifiedValidationResult:
    """统一验证入口 — 一次调用完成所有检查

    Args:
        code_files: 代码文件字典 {路径: 内容}
        scene_desc: 场景描述（可选）
        gdm: Game Design Model（可选）
        file_metadata: 文件元数据（可选）

    Returns:
        统一验证结果
    """
    result = UnifiedValidationResult()

    if not code_files:
        result.warnings.append({"check": "input", "message": "无代码文件"})
        return result

    # ========== 第1层：GDScript 语法检查 ==========
    gd_files = {p: c for p, c in code_files.items() if p.endswith(".gd")}
    syntax_errors = set()  # 用于去重

    for path, content in gd_files.items():
        # 括号平衡
        if content.count("(") != content.count(")"):
            err_key = f"{path}:paren"
            if err_key not in syntax_errors:
                syntax_errors.add(err_key)
                result.errors.append({
                    "check": "syntax",
                    "message": f"小括号不匹配: {path}",
                    "file": path,
                })
        if content.count("[") != content.count("]"):
            err_key = f"{path}:bracket"
            if err_key not in syntax_errors:
                syntax_errors.add(err_key)
                result.errors.append({
                    "check": "syntax",
                    "message": f"方括号不匹配: {path}",
                    "file": path,
                })

        # 缩进一致性检查
        has_tab = False
        has_space = False
        for line in content.split("\n"):
            if line.startswith("\t"):
                has_tab = True
            elif line.startswith("    "):
                has_space = True

        if has_tab and has_space:
            err_key = f"{path}:indent"
            if err_key not in syntax_errors:
                syntax_errors.add(err_key)
                result.errors.append({
                    "check": "syntax",
                    "message": f"缩进不一致：混用 Tab 和空格: {path}",
                    "file": path,
                })

        # func 定义检查
        for i, line in enumerate(content.split("\n")):
            stripped = line.strip()
            if stripped.startswith("func ") and ":" not in stripped:
                result.errors.append({
                    "check": "syntax",
                    "message": f"第{i+1}行: func 定义缺少冒号: {path}",
                    "file": path,
                    "line": i + 1,
                })

    # ========== 第2层：Godot 兼容性检查 ==========
    try:
        from src.utils.godot_compatibility_validator import validate_godot_compatibility
        compat = validate_godot_compatibility(code_files)
        existing_messages = {e.get("message", "") for e in result.errors}
        for err in compat.errors:
            if err.get("message", "") not in existing_messages:
                result.errors.append(err)
        for warn in compat.warnings:
            if warn.get("message", "") not in {w.get("message", "") for w in result.warnings}:
                result.warnings.append(warn)
    except Exception as e:
        logger.warning("godot_compat_check_failed", error=str(e))

    # ========== 第3层：代码与场景一致性（仅检查有场景时） ==========
    if scene_desc:
        try:
            from src.utils.consistency_validator import validate_code_scene_consistency
            consistency = validate_code_scene_consistency(
                code_files=code_files,
                scene_desc=scene_desc,
                gdm=gdm,
                file_metadata=file_metadata,
            )
            existing_messages = {e.get("message", "") for e in result.errors}
            for err in consistency.errors:
                if err.get("message", "") not in existing_messages:
                    result.errors.append(err)
            for warn in consistency.warnings:
                if warn.get("message", "") not in {w.get("message", "") for w in result.warnings}:
                    result.warnings.append(warn)
        except Exception as e:
            logger.warning("consistency_check_failed", error=str(e))

    return result
