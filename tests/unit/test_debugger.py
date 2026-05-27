"""测试 DebuggerAgent 修复应用"""

import pytest
from unittest.mock import AsyncMock, patch
from src.agents.debugger import DebuggerAgent


@pytest.fixture
def agent(sample_config):
    return DebuggerAgent(sample_config)


@pytest.fixture
def state_with_code():
    return {
        "code_generated": {
            "Assets/Scripts/Player/PlayerController.cs": (
                "public class PlayerController : MonoBehaviour\n"
                "{\n"
                "    private void Update()\n"
                "    {\n"
                "        Move();\n"
                "    }\n"
                "}"
            ),
        },
        "fix_history": [],
        "fix_attempts": 0,
        "error_log": ["Assets/Scripts/Player/PlayerController.cs(5,10): error CS0246: 'Move' not found"],
    }


class TestDebuggerApply:
    def test_apply_replace_success(self, agent):
        code = {"f.cs": "line1\nold_code\nline3"}
        ok = agent._apply_replace(code, "f.cs", {"old_code": "old_code", "new_code": "new_code"})
        assert ok is True
        assert "new_code" in code["f.cs"]
        assert "old_code" not in code["f.cs"]

    def test_apply_replace_file_missing(self, agent):
        code = {"f.cs": "content"}
        ok = agent._apply_replace(code, "missing.cs", {"old_code": "x", "new_code": "y"})
        assert ok is False

    def test_apply_replace_old_not_found(self, agent):
        code = {"f.cs": "content"}
        ok = agent._apply_replace(code, "f.cs", {"old_code": "not_there", "new_code": "y"})
        assert ok is False

    def test_apply_insert_success(self, agent):
        code = {"f.cs": "line0\nline1\nline2"}
        ok = agent._apply_insert(code, "f.cs", {"new_code": "inserted", "line": 1})
        assert ok is True
        lines = code["f.cs"].split("\n")
        assert lines[1] == "inserted"
        assert lines[2] == "line1"

    def test_apply_insert_file_missing(self, agent):
        code = {"f.cs": "content"}
        ok = agent._apply_insert(code, "missing.cs", {"new_code": "x", "line": 0})
        assert ok is False

    def test_apply_delete_success(self, agent):
        code = {"f.cs": "line1\nremove_me\nline3"}
        ok = agent._apply_delete(code, "f.cs", {"old_code": "remove_me\n"})
        assert ok is True
        assert "remove_me" not in code["f.cs"]

    def test_apply_delete_file_missing(self, agent):
        code = {"f.cs": "content"}
        ok = agent._apply_delete(code, "missing.cs", {"old_code": "x"})
        assert ok is False

    def test_apply_delete_old_not_found(self, agent):
        code = {"f.cs": "content"}
        ok = agent._apply_delete(code, "f.cs", {"old_code": "not_there"})
        assert ok is False


class TestDebuggerAnalyzeAndFix:
    @pytest.mark.asyncio
    async def test_no_errors_returns_unchanged(self, agent, state_with_code):
        result = await agent.analyze_and_fix(state_with_code, [])
        assert result["fix_attempts"] == 0
        assert "code_generated" not in result

    @pytest.mark.asyncio
    async def test_llm_success_applies_fixes(self, agent, state_with_code):
        mock_result = {
            "error_type": "missing_method",
            "error_message": "Move not found",
            "root_cause": "Move method missing",
            "fixes": [
                {
                    "file": "Assets/Scripts/Player/PlayerController.cs",
                    "changes": [
                        {
                            "type": "insert",
                            "new_code": "    private void Move() { transform.Translate(Vector3.right); }",
                            "line": 6,
                        }
                    ],
                }
            ],
            "confidence": 0.9,
        }
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.analyze_and_fix(state_with_code, state_with_code["error_log"])

        assert "code_generated" in result
        assert "private void Move()" in result["code_generated"]["Assets/Scripts/Player/PlayerController.cs"]
        assert len(result["fix_history"]) == 1
        assert result["fix_history"][0]["success"] is True

    @pytest.mark.asyncio
    async def test_llm_replace_applied(self, agent, state_with_code):
        mock_result = {
            "error_type": "logic",
            "error_message": "wrong call",
            "root_cause": "Move() should be Move(Vector3.right)",
            "fixes": [
                {
                    "file": "Assets/Scripts/Player/PlayerController.cs",
                    "changes": [
                        {
                            "type": "replace",
                            "old_code": "Move();",
                            "new_code": "Move(Vector3.right);",
                        }
                    ],
                }
            ],
            "confidence": 0.8,
        }
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.analyze_and_fix(state_with_code, state_with_code["error_log"])

        assert "Move(Vector3.right);" in result["code_generated"]["Assets/Scripts/Player/PlayerController.cs"]
        assert "Move();" not in result["code_generated"]["Assets/Scripts/Player/PlayerController.cs"]

    @pytest.mark.asyncio
    async def test_llm_parse_error_fallback(self, agent, state_with_code):
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value={"parse_error": True}):
            result = await agent.analyze_and_fix(state_with_code, state_with_code["error_log"])

        assert result["fix_history"][-1]["success"] is False
        assert "code_generated" in result

    @pytest.mark.asyncio
    async def test_llm_exception_fallback(self, agent, state_with_code):
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, side_effect=Exception("timeout")):
            result = await agent.analyze_and_fix(state_with_code, state_with_code["error_log"])

        assert result["fix_history"][-1]["success"] is False
        assert "code_generated" in result

    @pytest.mark.asyncio
    async def test_apply_failure_recorded(self, agent, state_with_code):
        mock_result = {
            "error_type": "test",
            "error_message": "test",
            "root_cause": "test",
            "fixes": [
                {
                    "file": "Assets/Scripts/Player/PlayerController.cs",
                    "changes": [
                        {
                            "type": "replace",
                            "old_code": "THIS_DOES_NOT_EXIST",
                            "new_code": "replacement",
                        }
                    ],
                }
            ],
            "confidence": 0.5,
        }
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.analyze_and_fix(state_with_code, state_with_code["error_log"])

        assert result["fix_history"][-1]["success"] is False
        assert result["fix_history"][-1]["apply_results"][0]["success"] is False
