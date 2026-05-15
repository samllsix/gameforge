"""GameForge - 评测脚本

用于评估生成代码的质量和效果。
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime


class CodeEvaluator:
    """代码评测器"""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "files": [],
            "metrics": {},
            "summary": {},
        }

    def evaluate(self) -> Dict[str, Any]:
        """运行完整评测"""
        print("=" * 60)
        print("GameForge 代码评测报告")
        print("=" * 60)

        # 1. 文件统计
        self._evaluate_files()

        # 2. 代码质量检查
        self._evaluate_code_quality()

        # 3. 生成评测报告
        self._generate_report()

        return self.results

    def _evaluate_files(self):
        """评测生成的文件"""
        print("\n[1] 文件统计")
        print("-" * 40)

        if not self.output_dir.exists():
            print("  [X] 输出目录不存在")
            return

        cs_files = list(self.output_dir.rglob("*.cs"))
        test_files = [f for f in cs_files if "Test" in f.name]
        source_files = [f for f in cs_files if "Test" not in f.name]

        self.results["files"] = [
            {
                "path": str(f.relative_to(self.output_dir)),
                "size": f.stat().st_size,
                "type": "test" if "Test" in f.name else "source",
            }
            for f in cs_files
        ]

        print(f"  [*] 总文件数: {len(cs_files)}")
        print(f"  [>] 源代码文件: {len(source_files)}")
        print(f"  [T] 测试文件: {len(test_files)}")

        for f in cs_files:
            rel_path = f.relative_to(self.output_dir)
            size_kb = f.stat().st_size / 1024
            file_type = "[T]" if "Test" in f.name else "[>]"
            print(f"    {file_type} {rel_path} ({size_kb:.1f} KB)")

    def _evaluate_code_quality(self):
        """评测代码质量"""
        print("\n[2] 代码质量检查")
        print("-" * 40)

        quality_checks = {
            "has_namespace": False,
            "has_comments": False,
            "has_region": False,
            "has_null_check": False,
            "has_serialized_field": False,
            "follows_naming": False,
        }

        for cs_file in self.output_dir.rglob("*.cs"):
            if "Test" in cs_file.name:
                continue

            content = cs_file.read_text(encoding="utf-8")

            # 检查命名空间
            if "namespace" in content:
                quality_checks["has_namespace"] = True

            # 检查注释
            if "///" in content or "//" in content:
                quality_checks["has_comments"] = True

            # 检查Region
            if "#region" in content:
                quality_checks["has_region"] = True

            # 检查空引用检查
            if "null" in content.lower() or "if (" in content:
                quality_checks["has_null_check"] = True

            # 检查SerializeField
            if "[SerializeField]" in content:
                quality_checks["has_serialized_field"] = True

            # 检查命名规范
            if "private" in content and "_" in content:
                quality_checks["follows_naming"] = True

        self.results["metrics"]["quality_checks"] = quality_checks

        passed = sum(1 for v in quality_checks.values() if v)
        total = len(quality_checks)

        for check, passed in quality_checks.items():
            status = "[OK]" if passed else "[--]"
            print(f"  {status} {check}")

        print(f"\n  质量得分: {passed}/{total} ({passed/total*100:.0f}%)")

    def _generate_report(self):
        """生成评测报告"""
        print("\n[3] 评测总结")
        print("-" * 40)

        # 计算总体得分
        quality_checks = self.results["metrics"].get("quality_checks", {})
        passed_checks = sum(1 for v in quality_checks.values() if v)
        total_checks = len(quality_checks)

        file_count = len(self.results["files"])
        source_count = sum(1 for f in self.results["files"] if f["type"] == "source")
        test_count = sum(1 for f in self.results["files"] if f["type"] == "test")

        self.results["summary"] = {
            "total_files": file_count,
            "source_files": source_count,
            "test_files": test_count,
            "quality_score": passed_checks / total_checks if total_checks > 0 else 0,
            "has_tests": test_count > 0,
            "test_coverage": "基础" if test_count > 0 else "无",
        }

        summary = self.results["summary"]

        print(f"  [*] 生成文件数: {summary['total_files']}")
        print(f"  [>] 源代码文件: {summary['source_files']}")
        print(f"  [T] 测试文件: {summary['test_files']}")
        print(f"  [%] 代码质量得分: {summary['quality_score']*100:.0f}%")
        print(f"  [~] 测试覆盖: {summary['test_coverage']}")

        # 总体评价
        print("\n" + "=" * 60)
        if summary["quality_score"] >= 0.8 and summary["has_tests"]:
            print("[EXCELLENT] 评测结果: 优秀")
            print("   代码质量高，包含测试用例")
        elif summary["quality_score"] >= 0.6:
            print("[GOOD] 评测结果: 良好")
            print("   代码质量可接受，建议增加测试")
        else:
            print("[IMPROVE] 评测结果: 需改进")
            print("   建议优化代码结构和添加测试")
        print("=" * 60)

        # 保存报告
        report_path = self.output_dir / "evaluation_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"\n[RPT] 详细报告已保存: {report_path}")


def main():
    """主函数"""
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "output"
    evaluator = CodeEvaluator(output_dir)
    evaluator.evaluate()


if __name__ == "__main__":
    main()
