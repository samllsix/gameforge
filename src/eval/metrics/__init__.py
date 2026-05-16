"""GameForge - 评测指标模块

定义和计算代码质量、任务完成度等评测指标。
"""

import re
import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field, asdict


@dataclass
class MetricResult:
    """单个指标的评测结果"""
    name: str
    value: float
    max_value: float = 100.0
    unit: str = "%"
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def score(self) -> float:
        return (self.value / self.max_value) * 100 if self.max_value > 0 else 0


@dataclass
class EvalReport:
    """评测报告"""
    project_name: str
    metrics: List[MetricResult] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def overall_score(self) -> float:
        if not self.metrics:
            return 0.0
        return sum(m.score for m in self.metrics) / len(self.metrics)

    def add_metric(self, name: str, value: float, max_value: float = 100.0, unit: str = "%", **details):
        self.metrics.append(MetricResult(
            name=name, value=value, max_value=max_value, unit=unit, details=details
        ))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "overall_score": self.overall_score,
            "metrics": [asdict(m) for m in self.metrics],
            "created_at": self.created_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, output_dir: str = "data/eval_reports"):
        os.makedirs(output_dir, exist_ok=True)
        filename = f"eval_{self.project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        return filepath


class CodeQualityMetrics:
    """代码质量评测指标"""

    @staticmethod
    def compile_success_rate(code_files: Dict[str, str]) -> MetricResult:
        """计算编译成功率

        Args:
            code_files: 代码文件字典 {路径: 内容}

        Returns:
            编译成功率指标
        """
        if not code_files:
            return MetricResult(name="compile_success", value=0, details={"reason": "no_files"})

        success_count = 0
        errors = []

        for path, content in code_files.items():
            if not path.endswith(".cs"):
                continue

            is_valid, file_errors = CodeQualityMetrics._validate_csharp_basics(content)
            if is_valid:
                success_count += 1
            else:
                errors.extend([f"{path}: {e}" for e in file_errors])

        cs_files = {p: c for p, c in code_files.items() if p.endswith(".cs")}
        if not cs_files:
            return MetricResult(name="compile_success", value=100, details={"reason": "no_cs_files"})

        rate = (success_count / len(cs_files)) * 100
        return MetricResult(
            name="compile_success",
            value=rate,
            details={"success": success_count, "total": len(cs_files), "errors": errors[:10]}
        )

    @staticmethod
    def code_quality_score(content: str) -> MetricResult:
        """计算代码质量分数

        Args:
            content: 代码内容

        Returns:
            代码质量指标
        """
        score = 100.0
        issues = []

        lines = content.split("\n")
        total_lines = len(lines)
        if total_lines == 0:
            return MetricResult(name="code_quality", value=0)

        comment_lines = sum(1 for l in lines if l.strip().startswith("//") or l.strip().startswith("///"))
        comment_ratio = comment_lines / total_lines
        if comment_ratio < 0.05:
            score -= 10
            issues.append("注释率过低")

        max_indent = 0
        for line in lines:
            stripped = line.lstrip()
            if stripped:
                indent = len(line) - len(stripped)
                max_indent = max(max_indent, indent)
        if max_indent > 16:
            score -= 15
            issues.append(f"嵌套层级过深({max_indent // 4}层)")

        if total_lines > 500:
            score -= 10
            issues.append(f"文件过长({total_lines}行)")

        long_lines = sum(1 for l in lines if len(l) > 120)
        if long_lines > 5:
            score -= 5
            issues.append(f"{long_lines}行超过120字符")

        magic_numbers = re.findall(r'(?<!\w)\d{2,}(?!\w)', content)
        if len(magic_numbers) > 10:
            score -= 5
            issues.append("存在大量魔术数字")

        return MetricResult(
            name="code_quality",
            value=max(0, score),
            details={"issues": issues, "comment_ratio": comment_ratio, "total_lines": total_lines}
        )

    @staticmethod
    def naming_convention_score(content: str) -> MetricResult:
        """检查命名规范

        Args:
            content: 代码内容

        Returns:
            命名规范指标
        """
        score = 100.0
        violations = []

        private_fields = re.findall(r'(?:private|protected)\s+\w+\s+(_?\w+)', content)
        for field_name in private_fields:
            if not field_name.startswith("_"):
                violations.append(f"私有字段 '{field_name}' 应以_开头")
                score -= 2

        public_methods = re.findall(r'public\s+(?:static\s+)?(?:async\s+)?\w+\s+(\w+)\s*\(', content)
        for method_name in public_methods:
            if method_name[0].isupper() is False:
                violations.append(f"公共方法 '{method_name}' 应使用PascalCase")
                score -= 2

        local_vars = re.findall(r'(?:var|int|float|string|bool)\s+(\w+)', content)
        for var_name in local_vars:
            if var_name[0].isupper() and not var_name.isupper():
                violations.append(f"局部变量 '{var_name}' 应使用camelCase")
                score -= 1

        return MetricResult(
            name="naming_convention",
            value=max(0, score),
            details={"violations": violations[:20]}
        )

    @staticmethod
    def _validate_csharp_basics(content: str):
        """基础C#语法验证"""
        errors = []

        if content.count("{") != content.count("}"):
            errors.append("大括号不匹配")
        if content.count("(") != content.count(")"):
            errors.append("小括号不匹配")

        return len(errors) == 0, errors


class TaskCompletionMetrics:
    """任务完成度评测指标"""

    @staticmethod
    def task_completion_rate(tasks: List[Dict[str, Any]]) -> MetricResult:
        """计算任务完成率

        Args:
            tasks: 任务列表

        Returns:
            任务完成率指标
        """
        if not tasks:
            return MetricResult(name="task_completion", value=0, details={"reason": "no_tasks"})

        completed = sum(1 for t in tasks if t.get("status") == "completed")
        rate = (completed / len(tasks)) * 100

        return MetricResult(
            name="task_completion",
            value=rate,
            details={"completed": completed, "total": len(tasks)}
        )

    @staticmethod
    def fix_efficiency(fix_history: List[Dict[str, Any]]) -> MetricResult:
        """计算修复效率

        Args:
            fix_history: 修复历史

        Returns:
            修复效率指标
        """
        if not fix_history:
            return MetricResult(name="fix_efficiency", value=100, details={"reason": "no_fixes_needed"})

        successful = sum(1 for f in fix_history if f.get("success", False))
        total = len(fix_history)
        rate = (successful / total) * 100

        return MetricResult(
            name="fix_efficiency",
            value=rate,
            details={"successful": successful, "total": total, "attempts_needed": total}
        )


def run_evaluation(
    project_name: str,
    code_files: Optional[Dict[str, str]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    fix_history: Optional[List[Dict[str, Any]]] = None,
) -> EvalReport:
    """运行完整评测

    Args:
        project_name: 项目名称
        code_files: 代码文件字典
        tasks: 任务列表
        fix_history: 修复历史

    Returns:
        评测报告
    """
    report = EvalReport(project_name=project_name)

    if code_files:
        compile_result = CodeQualityMetrics.compile_success_rate(code_files)
        report.metrics.append(compile_result)

        quality_scores = []
        for path, content in code_files.items():
            if path.endswith(".cs"):
                quality = CodeQualityMetrics.code_quality_score(content)
                quality_scores.append(quality.value)
        if quality_scores:
            avg_quality = sum(quality_scores) / len(quality_scores)
            report.add_metric("avg_code_quality", avg_quality, details={"file_count": len(quality_scores)})

        naming_scores = []
        for path, content in code_files.items():
            if path.endswith(".cs"):
                naming = CodeQualityMetrics.naming_convention_score(content)
                naming_scores.append(naming.value)
        if naming_scores:
            avg_naming = sum(naming_scores) / len(naming_scores)
            report.add_metric("avg_naming_convention", avg_naming)

    if tasks:
        completion = TaskCompletionMetrics.task_completion_rate(tasks)
        report.metrics.append(completion)

    if fix_history:
        efficiency = TaskCompletionMetrics.fix_efficiency(fix_history)
        report.metrics.append(efficiency)

    return report
