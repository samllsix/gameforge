"""GameForge - 静态代码校验器

对生成的Unity C#代码进行静态检查，发现常见问题。
"""

import re
from typing import Any, Dict, List


class ValidationResult:
    """校验结果"""

    def __init__(self):
        self.errors: List[Dict[str, str]] = []
        self.warnings: List[Dict[str, str]] = []

    @property
    def has_issues(self) -> bool:
        return len(self.errors) > 0 or len(self.warnings) > 0

    def add_error(self, file_path: str, message: str, line: int = 0):
        self.errors.append({"file": file_path, "message": message, "line": line, "level": "error"})

    def add_warning(self, file_path: str, message: str, line: int = 0):
        self.warnings.append({"file": file_path, "message": message, "line": line, "level": "warning"})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "passed": len(self.errors) == 0,
        }


def validate_unity_code(code_files: Dict[str, str]) -> ValidationResult:
    """校验Unity C#代码文件

    Args:
        code_files: {file_path: code_content} 字典

    Returns:
        ValidationResult 校验结果
    """
    result = ValidationResult()

    for file_path, content in code_files.items():
        if not file_path.endswith(".cs"):
            continue

        _check_class_filename_match(file_path, content, result)
        _check_namespace_valid(content, file_path, result)
        _check_unity_lifecycle_spelling(content, file_path, result)
        _check_findobjectoftype_usage(content, file_path, result)
        _check_missing_using(content, file_path, result)
        _check_bracket_balance(content, file_path, result)

    return result


def _check_class_filename_match(file_path: str, content: str, result: ValidationResult):
    """检查类名与文件名是否一致"""
    filename = file_path.rsplit("/", 1)[-1].replace(".cs", "")

    # 找到主类名
    class_match = re.search(r'public\s+(?:partial\s+)?(?:class|struct|interface|enum)\s+(\w+)', content)
    if class_match:
        class_name = class_match.group(1)
        if class_name != filename:
            result.add_warning(
                file_path,
                f"类名 '{class_name}' 与文件名 '{filename}' 不一致"
            )


def _check_namespace_valid(content: str, file_path: str, result: ValidationResult):
    """检查namespace是否合法"""
    ns_match = re.search(r'namespace\s+([\w.]+)', content)
    if ns_match:
        ns = ns_match.group(1)
        # 检查是否以数字开头
        if ns[0].isdigit():
            result.add_error(file_path, f"namespace '{ns}' 不能以数字开头")
        # 检查是否包含非法字符
        if re.search(r'[^a-zA-Z0-9_.]', ns):
            result.add_error(file_path, f"namespace '{ns}' 包含非法字符")


def _check_unity_lifecycle_spelling(content: str, file_path: str, result: ValidationResult):
    """检查Unity生命周期方法拼写"""
    # 常见拼写错误
    common_typos = {
        "Awkae": "Awake",
        "Updte": "Update",
        "FixdUpdate": "FixedUpdate",
        "OnEnabel": "OnEnable",
        "OnDisabel": "OnDisable",
        "OnDestory": "OnDestroy",
        "OnCollisonEnter": "OnCollisionEnter",
        "OnTrigerEnter": "OnTriggerEnter",
        "Star": "Start",  # 只匹配独立的Star方法
    }

    for typo, correct in common_typos.items():
        pattern = rf'\bprivate\s+void\s+{typo}\b'
        if re.search(pattern, content):
            result.add_error(file_path, f"生命周期方法拼写错误: '{typo}' -> '{correct}'")


def _check_findobjectoftype_usage(content: str, file_path: str, result: ValidationResult):
    """检测FindObjectOfType滥用（性能警告）"""
    # 旧API
    old_count = len(re.findall(r'FindObjectOfType\b(?!s)', content))
    if old_count > 0:
        result.add_warning(
            file_path,
            f"使用了已弃用的 FindObjectOfType（{old_count}次），建议使用 FindFirstObjectByType 或依赖注入"
        )

    # 新API在Update中的使用
    in_update = False
    for line in content.split("\n"):
        if "void Update()" in line or "void FixedUpdate()" in line:
            in_update = True
        elif re.match(r'\s*private void \w+', line):
            in_update = False
        if in_update and ("FindFirstObjectByType" in line or "FindObjectsByType" in line):
            result.add_warning(file_path, "在Update中调用FindObject*会导致性能问题，建议缓存引用")
            break


def _check_missing_using(content: str, file_path: str, result: ValidationResult):
    """检查是否缺少必要的using"""
    if "List<" in content and "using System.Collections.Generic" not in content:
        result.add_warning(file_path, "使用了 List<> 但缺少 using System.Collections.Generic")

    if "IEnumerator" in content and "using System.Collections" not in content:
        result.add_warning(file_path, "使用了 IEnumerator 但缺少 using System.Collections")

    if re.search(r'TextMeshPro|TMP_', content) and "using TMPro" not in content:
        result.add_warning(file_path, "使用了 TextMeshPro 但缺少 using TMPro")


def _check_bracket_balance(content: str, file_path: str, result: ValidationResult):
    """检查花括号是否平衡"""
    opens = content.count("{")
    closes = content.count("}")
    if opens != closes:
        result.add_error(file_path, f"花括号不平衡: {{ = {opens}, }} = {closes}")
