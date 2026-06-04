"""GameForge - 统一验证入口

整合三个验证器，提供统一调用接口，避免重复检查。
检查顺序：语法 → Unity兼容性 → 一致性，每层只检查上一层未覆盖的项。

注意：所有检查都考虑 Unity 编译环境兼容性。
"""

from typing import Any, Dict, List
from dataclasses import dataclass, field


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

    # ========== 第1层：C# 语法检查 ==========
    cs_files = {p: c for p, c in code_files.items() if p.endswith(".cs")}
    syntax_errors = set()  # 用于去重

    for path, content in cs_files.items():
        # 括号平衡
        if content.count("{") != content.count("}"):
            err_key = f"{path}:bracket"
            if err_key not in syntax_errors:
                syntax_errors.add(err_key)
                result.errors.append({
                    "check": "syntax",
                    "message": f"大括号不匹配: {path}",
                    "file": path,
                })
        if content.count("(") != content.count(")"):
            err_key = f"{path}:paren"
            if err_key not in syntax_errors:
                syntax_errors.add(err_key)
                result.errors.append({
                    "check": "syntax",
                    "message": f"小括号不匹配: {path}",
                    "file": path,
                })

        # using 缺失检查（Unity 必需）
        if "using UnityEngine" not in content and "MonoBehaviour" in content:
            err_key = f"{path}:using_unity"
            if err_key not in syntax_errors:
                syntax_errors.add(err_key)
                result.errors.append({
                    "check": "unity_compatibility",
                    "message": f"缺少 using UnityEngine: {path}",
                    "file": path,
                })

        # 类名与文件名一致性
        import re
        class_match = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)', content)
        if class_match:
            class_name = class_match.group(1)
            file_name = path.rsplit("/", 1)[-1].replace(".cs", "")
            if class_name != file_name:
                result.warnings.append({
                    "check": "naming",
                    "message": f"类名 {class_name} 与文件名 {file_name} 不一致: {path}",
                    "file": path,
                })

        # Unity 生命周期方法拼写检查
        wrong_lifecycles = {
            "OnAwake": "Awake",
            "OnStart": "Start",
            "OnUpdate": "Update",
            "OnFixedUpdate": "FixedUpdate",
            "OnLateUpdate": "LateUpdate",
        }
        for wrong, correct in wrong_lifecycles.items():
            if f"void {wrong}()" in content or f"void {wrong} ()" in content:
                result.errors.append({
                    "check": "unity_lifecycle",
                    "message": f"Unity生命周期方法拼写错误: {wrong} → {correct}: {path}",
                    "file": path,
                })

        # FindObjectOfType 滥用警告
        find_count = content.count("FindObjectOfType")
        if find_count > 2:
            result.warnings.append({
                "check": "performance",
                "message": f"FindObjectOfType 调用 {find_count} 次，建议缓存引用: {path}",
                "file": path,
            })

    # ========== 第2层：Unity 兼容性检查（仅检查第1层未覆盖的） ==========
    try:
        from src.utils.unity_compatibility_validator import validate_unity_compatibility
        compat = validate_unity_compatibility(code_files, scene_desc, gdm)
        existing_messages = {e.get("message", "") for e in result.errors}
        for err in compat.errors:
            if err.get("message", "") not in existing_messages:
                result.errors.append(err)
        for warn in compat.warnings:
            if warn.get("message", "") not in {w.get("message", "") for w in result.warnings}:
                result.warnings.append(warn)
        result.suggestions.extend(compat.suggestions)
    except Exception:
        pass

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
        except Exception:
            pass

    return result
