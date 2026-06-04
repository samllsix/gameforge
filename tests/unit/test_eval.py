"""测试 Eval系统模块"""

import pytest
import json
import os
import tempfile
from src.eval.metrics import MetricResult, EvalReport, CodeQualityMetrics
from src.eval.test_cases import TestCase, TestCaseManager


class TestMetricResult:
    """测试 MetricResult"""

    def test_init(self):
        """初始化"""
        result = MetricResult(name="test", value=80)
        assert result.name == "test"
        assert result.value == 80
        assert result.max_value == 100.0
        assert result.unit == "%"

    def test_score_calculation(self):
        """分数计算"""
        result = MetricResult(name="test", value=80, max_value=100)
        assert result.score == 80.0

    def test_score_partial(self):
        """部分分数"""
        result = MetricResult(name="test", value=50, max_value=200)
        assert result.score == 25.0

    def test_score_zero_max(self):
        """最大值为0"""
        result = MetricResult(name="test", value=50, max_value=0)
        assert result.score == 0

    def test_default_timestamp(self):
        """默认时间戳"""
        result = MetricResult(name="test", value=50)
        assert result.timestamp is not None

    def test_details_default(self):
        """默认详情为空字典"""
        result = MetricResult(name="test", value=50)
        assert result.details == {}


class TestEvalReport:
    """测试 EvalReport"""

    def test_init(self):
        """初始化"""
        report = EvalReport(project_name="test_project")
        assert report.project_name == "test_project"
        assert report.metrics == []

    def test_overall_score_empty(self):
        """空报告的总分"""
        report = EvalReport(project_name="test")
        assert report.overall_score == 0.0

    def test_overall_score_with_metrics(self):
        """有指标的总分"""
        report = EvalReport(project_name="test")
        report.add_metric("metric1", 80)
        report.add_metric("metric2", 60)
        assert report.overall_score == 70.0

    def test_add_metric(self):
        """添加指标"""
        report = EvalReport(project_name="test")
        report.add_metric("compile_success", 95, max_value=100, unit="%")
        assert len(report.metrics) == 1
        assert report.metrics[0].name == "compile_success"
        assert report.metrics[0].value == 95

    def test_to_dict(self):
        """转换为字典"""
        report = EvalReport(project_name="test")
        report.add_metric("metric1", 80)
        data = report.to_dict()
        assert data["project_name"] == "test"
        assert "overall_score" in data
        assert "metrics" in data
        assert len(data["metrics"]) == 1

    def test_to_json(self):
        """转换为JSON"""
        report = EvalReport(project_name="test")
        report.add_metric("metric1", 80)
        json_str = report.to_json()
        data = json.loads(json_str)
        assert data["project_name"] == "test"

    def test_save(self, tmp_path):
        """保存报告"""
        report = EvalReport(project_name="test")
        report.add_metric("metric1", 80)
        filepath = report.save(output_dir=str(tmp_path))
        assert os.path.exists(filepath)
        assert filepath.endswith(".json")

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["project_name"] == "test"


class TestCodeQualityMetrics:
    """测试 CodeQualityMetrics"""

    def test_compile_success_rate_empty(self):
        """空文件"""
        result = CodeQualityMetrics.compile_success_rate({})
        assert result.value == 0
        assert result.details["reason"] == "no_files"

    def test_compile_success_rate_no_cs(self):
        """无CS文件"""
        result = CodeQualityMetrics.compile_success_rate({
            "README.md": "# Hello"
        })
        assert result.value == 100
        assert result.details["reason"] == "no_cs_files"

    def test_compile_success_rate_valid(self):
        """有效CS文件"""
        code = """
public class Player : MonoBehaviour
{
    void Start() { }
    void Update() { }
}
"""
        result = CodeQualityMetrics.compile_success_rate({
            "Assets/Player.cs": code
        })
        assert result.value == 100

    def test_compile_success_rate_invalid(self):
        """无效CS文件"""
        code = """
public class Player : MonoBehaviour
{
    void Start() {
    // missing closing brace
"""
        result = CodeQualityMetrics.compile_success_rate({
            "Assets/Player.cs": code
        })
        assert result.value < 100

    def test_compile_success_rate_mixed(self):
        """混合文件"""
        valid_code = """
public class Player : MonoBehaviour
{
    void Start() { }
}
"""
        invalid_code = """
public class Enemy : MonoBehaviour
{
    void Start() {
"""
        result = CodeQualityMetrics.compile_success_rate({
            "Assets/Player.cs": valid_code,
            "Assets/Enemy.cs": invalid_code,
        })
        assert result.value == 50

    def test_code_quality_score(self):
        """代码质量评分"""
        code = """
public class Simple : MonoBehaviour
{
    // Player health
    private int health = 100;

    /// <summary>
    /// Take damage
    /// </summary>
    public void TakeDamage(int amount)
    {
        health -= amount;
    }

    public void Heal(int amount)
    {
        health += amount;
    }
}
"""
        result = CodeQualityMetrics.code_quality_score(code)
        assert result.name == "code_quality"
        assert result.value > 0

    def test_naming_convention_score_valid(self):
        """命名规范 - 有效"""
        code = """
public class PlayerController : MonoBehaviour
{
    private int playerHealth;
    public float moveSpeed;
    public void TakeDamage(int amount) { }
}
"""
        result = CodeQualityMetrics.naming_convention_score(code)
        assert result.value > 50

    def test_naming_convention_score_invalid(self):
        """命名规范 - 无效"""
        code = """
public class player_controller : MonoBehaviour
{
    private int HP;
    public float SPEED;
    public void take_damage(int amt) { }
}
"""
        result = CodeQualityMetrics.naming_convention_score(code)
        assert result.value < 100


class TestTestCase:
    """测试 TestCase"""

    def test_init(self):
        """初始化"""
        case = TestCase(
            id="tc_001",
            name="测试用例",
            description="描述",
            requirements="需求"
        )
        assert case.id == "tc_001"
        assert case.name == "测试用例"
        assert case.engine == "unity"
        assert case.difficulty == "medium"

    def test_to_dict(self):
        """转换为字典"""
        case = TestCase(
            id="tc_001",
            name="测试",
            description="描述",
            requirements="需求",
            expected_features=["feature1"]
        )
        data = case.to_dict()
        assert data["id"] == "tc_001"
        assert data["expected_features"] == ["feature1"]


class TestTestCaseManager:
    """测试 TestCaseManager"""

    def test_init(self, tmp_path):
        """初始化"""
        manager = TestCaseManager(data_dir=str(tmp_path))
        assert manager.data_dir == str(tmp_path)

    def test_get_default_cases(self, tmp_path):
        """获取默认测试用例"""
        manager = TestCaseManager(data_dir=str(tmp_path))
        cases = manager.get_default_cases()
        assert len(cases) == 3
        assert all(isinstance(c, TestCase) for c in cases)
        assert cases[0].id == "tc_001"
        assert cases[1].id == "tc_002"
        assert cases[2].id == "tc_003"

    def test_save_and_load_case(self, tmp_path):
        """保存和加载测试用例"""
        manager = TestCaseManager(data_dir=str(tmp_path))
        case = TestCase(
            id="tc_test",
            name="测试",
            description="描述",
            requirements="需求"
        )
        manager.save_case(case)

        loaded = manager.load_case("tc_test")
        assert loaded is not None
        assert loaded.id == "tc_test"
        assert loaded.name == "测试"

    def test_load_nonexistent_case(self, tmp_path):
        """加载不存在的用例"""
        manager = TestCaseManager(data_dir=str(tmp_path))
        loaded = manager.load_case("nonexistent")
        assert loaded is None

    def test_list_cases_empty(self, tmp_path):
        """列出空目录的用例"""
        manager = TestCaseManager(data_dir=str(tmp_path))
        cases = manager.list_cases()
        assert len(cases) == 0

    def test_list_cases_with_files(self, tmp_path):
        """列出有文件的用例"""
        manager = TestCaseManager(data_dir=str(tmp_path))
        case1 = TestCase(id="tc_1", name="测试1", description="d", requirements="r")
        case2 = TestCase(id="tc_2", name="测试2", description="d", requirements="r")
        manager.save_case(case1)
        manager.save_case(case2)

        cases = manager.list_cases()
        assert len(cases) == 2
