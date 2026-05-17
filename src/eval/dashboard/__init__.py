"""GameForge - 评测面板模块

提供评测结果的可视化和报告生成功能。
"""

import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime


class EvalDashboard:
    """评测面板 - 生成评测报告和统计数据"""

    def __init__(self, output_dir: str = "data/eval_reports"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate_html_report(self, eval_data: Dict[str, Any]) -> str:
        """生成HTML评测报告

        Args:
            eval_data: 评测数据

        Returns:
            HTML报告路径
        """
        project_name = eval_data.get("project_name", "unknown")
        overall_score = eval_data.get("overall_score", 0)
        metrics = eval_data.get("metrics", [])

        metrics_html = ""
        for metric in metrics:
            score = metric.get("score", 0)
            color = "#4CAF50" if score >= 80 else "#FF9800" if score >= 60 else "#F44336"
            metrics_html += f"""
            <div class="metric">
                <h3>{metric.get('name', 'Unknown')}</h3>
                <div class="score" style="color: {color}">{score:.1f}%</div>
                <div class="bar"><div class="fill" style="width: {score}%; background: {color}"></div></div>
            </div>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>GameForge 评测报告 - {project_name}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        .overall {{ font-size: 48px; text-align: center; margin: 20px 0; }}
        .metric {{ margin: 20px 0; padding: 15px; background: #f9f9f9; border-radius: 5px; }}
        .metric h3 {{ margin: 0 0 10px 0; }}
        .score {{ font-size: 24px; font-weight: bold; }}
        .bar {{ height: 10px; background: #eee; border-radius: 5px; margin-top: 5px; }}
        .fill {{ height: 100%; border-radius: 5px; transition: width 0.3s; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>GameForge 评测报告</h1>
        <p>项目: {project_name}</p>
        <p>时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <div class="overall" style="color: {'#4CAF50' if overall_score >= 80 else '#FF9800' if overall_score >= 60 else '#F44336'}">
            {overall_score:.1f}%
        </div>
        <h2>评测指标</h2>
        {metrics_html}
    </div>
</body>
</html>"""

        filename = f"eval_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        return filepath

    def load_report(self, filepath: str) -> Dict[str, Any]:
        """加载评测报告

        Args:
            filepath: 报告文件路径

        Returns:
            报告数据
        """
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_reports(self) -> List[str]:
        """列出所有评测报告

        Returns:
            报告文件路径列表
        """
        reports = []
        if os.path.isdir(self.output_dir):
            for f in os.listdir(self.output_dir):
                if f.endswith((".json", ".html")):
                    reports.append(os.path.join(self.output_dir, f))
        return sorted(reports)
