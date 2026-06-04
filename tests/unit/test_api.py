"""测试 API端点"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端"""
    # 设置测试环境变量
    import os
    os.environ["GAMEFORGE_ENV"] = "development"
    os.environ.pop("GAMEFORGE_API_KEYS", None)

    from src.api.main import app
    return TestClient(app)


class TestRootEndpoint:
    """测试根路径"""

    def test_root_returns_info(self, client):
        """根路径返回API信息"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "GameForge API"
        assert "version" in data
        assert "security" in data

    def test_root_security_info(self, client):
        """根路径包含安全信息"""
        response = client.get("/")
        data = response.json()
        security = data["security"]
        assert "rate_limiting" in security
        assert "concurrency_limit" in security
        assert "input_validation" in security


class TestHealthEndpoint:
    """测试健康检查"""

    def test_health_check(self, client):
        """健康检查端点"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "concurrency" in data


class TestStatsEndpoint:
    """测试统计信息"""

    def test_get_stats(self, client):
        """获取统计信息"""
        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert "concurrency" in data


class TestAgentsEndpoint:
    """测试Agent列表"""

    def test_list_agents(self, client):
        """获取Agent列表"""
        response = client.get("/api/v1/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)
        assert len(data["agents"]) > 0

    def test_agent_info_structure(self, client):
        """Agent信息结构"""
        response = client.get("/api/v1/agents")
        data = response.json()
        agent = data["agents"][0]
        assert "name" in agent
        assert "description" in agent


class TestGenerateEndpoint:
    """测试代码生成"""

    def test_generate_missing_requirements(self, client):
        """缺少requirements字段"""
        response = client.post("/api/v1/generate", json={})
        assert response.status_code == 422

    def test_generate_empty_requirements(self, client):
        """空requirements"""
        response = client.post("/api/v1/generate", json={"requirements": ""})
        assert response.status_code == 422

    def test_generate_invalid_engine(self, client):
        """无效的引擎"""
        response = client.post("/api/v1/generate", json={
            "requirements": "create a game",
            "engine": "invalid_engine"
        })
        assert response.status_code == 422

    @patch("src.api.main.create_workflow")
    def test_generate_success(self, mock_workflow, client):
        """成功生成代码"""
        mock_workflow.return_value = MagicMock()
        response = client.post("/api/v1/generate", json={
            "requirements": "create a simple 2d platformer game",
            "engine": "unity"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "task_id" in data


class TestTaskPlanEndpoint:
    """测试任务规划"""

    def test_task_plan_missing_requirements(self, client):
        """缺少requirements"""
        response = client.post("/api/v1/plan", json={})
        assert response.status_code == 422

    @patch("src.api.main.create_workflow")
    def test_task_plan_success(self, mock_workflow, client):
        """成功创建任务规划"""
        mock_workflow.return_value = MagicMock()
        response = client.post("/api/v1/plan", json={
            "requirements": "create a space shooter game"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "task_id" in data


class TestTaskStatusEndpoint:
    """测试任务状态"""

    def test_task_status_not_found(self, client):
        """任务不存在"""
        response = client.get("/task/nonexistent_task_id")
        assert response.status_code == 404


class TestCompileEndpoint:
    """测试编译端点"""

    @patch("src.api.routes.UnityEditor")
    def test_compile_unity_not_found(self, mock_editor, client):
        """Unity不可用时返回错误"""
        mock_editor.return_value.validate.return_value = (False, "Unity not found")
        response = client.post("/api/v1/ext/compile", json={})
        assert response.status_code == 400


class TestImportEndpoint:
    """测试导入端点"""

    def test_import_missing_files(self, client):
        """缺少files字段"""
        response = client.post("/api/v1/ext/import", json={})
        assert response.status_code == 422

    @patch("src.api.routes.UnityEditor")
    def test_import_success(self, mock_editor, client):
        """成功导入文件"""
        mock_instance = MagicMock()
        mock_instance.validate.return_value = (True, "OK")
        mock_instance.import_files.return_value = MagicMock(
            success=True,
            imported_files=["Assets/test.cs"],
            failed_files=[],
            message="ok",
        )
        mock_editor.return_value = mock_instance

        response = client.post("/api/v1/ext/import", json={
            "files": {"Assets/test.cs": "public class Test {}"}
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["imported_files"] == ["Assets/test.cs"]


class TestEvalEndpoint:
    """测试评测端点"""

    def test_eval_default_project(self, client):
        """默认项目评测"""
        response = client.post("/api/v1/ext/eval", json={
            "project_name": "test"
        })
        assert response.status_code == 200
        data = response.json()
        assert "overall_score" in data


class TestDocumentEndpoints:
    """测试文档端点"""

    def test_openapi_json(self, client):
        """OpenAPI JSON可用"""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data

    def test_docs_page(self, client):
        """Swagger文档页面"""
        response = client.get("/docs")
        assert response.status_code == 200


class TestErrorHandling:
    """测试错误处理"""

    def test_404_not_found(self, client):
        """不存在的端点"""
        response = client.get("/nonexistent")
        assert response.status_code == 404

    def test_method_not_allowed(self, client):
        """错误的HTTP方法"""
        response = client.delete("/")
        assert response.status_code == 405


class TestMiddleware:
    """测试中间件"""

    def test_cors_headers(self, client):
        """CORS头存在"""
        response = client.options("/", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET"
        })
        # CORS中间件应该响应
        assert response.status_code in [200, 204]

    def test_security_headers(self, client):
        """安全头存在"""
        response = client.get("/")
        # 检查安全头
        assert "x-content-type-options" in response.headers or "X-Content-Type-Options" in response.headers
