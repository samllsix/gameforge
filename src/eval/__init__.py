"""GameForge - 评测模块

提供代码质量评测、任务完成度分析、评测报告生成等功能。
"""

from typing import Dict, Any, Optional, List


def run_evaluation(
    project_name: str,
    code_files: Optional[Dict[str, str]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    fix_history: Optional[List[Dict[str, Any]]] = None,
):
    """运行评测

    Args:
        project_name: 项目名称
        code_files: 代码文件字典
        tasks: 任务列表
        fix_history: 修复历史

    Returns:
        评测报告
    """
    from src.eval.metrics import run_evaluation as _run_eval
    return _run_eval(project_name, code_files, tasks, fix_history)


def get_metrics():
    """获取评测指标类

    Returns:
        指标类字典
    """
    from src.eval.metrics import CodeQualityMetrics, TaskCompletionMetrics
    return {
        "code_quality": CodeQualityMetrics,
        "task_completion": TaskCompletionMetrics,
    }
