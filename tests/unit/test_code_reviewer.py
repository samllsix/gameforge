"""测试 CodeReviewerAgent"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.agents.code_reviewer import CodeReviewerAgent


@pytest.fixture
def agent(sample_config):
    return CodeReviewerAgent(sample_config)


@pytest.fixture
def state_with_code():
    return {
        "code_generated": {
            "Assets/Scripts/Player/PlayerController.cs": "public class PlayerController : MonoBehaviour {}",
            "Assets/Scripts/Player/PlayerControllerTests.cs": "public class PlayerControllerTests {}",
        },
        "current_phase": "code_generated",
    }


class TestCodeReviewer:
    @pytest.mark.asyncio
    async def test_review_no_code_returns_not_passed(self, agent):
        state = {"code_generated": {}}
        result = await agent.review(state)
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_review_only_tests_skips(self, agent):
        state = {"code_generated": {"Assets/Tests/FooTests.cs": "class FooTests {}"}}
        result = await agent.review(state)
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_review_llm_parse_error_not_passed(self, agent, state_with_code):
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value={"parse_error": True}):
            result = await agent.review(state_with_code)
            assert result["passed"] is False
            assert result["status"] == "review_unavailable"
            assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_review_llm_exception_not_passed(self, agent, state_with_code):
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, side_effect=Exception("API timeout")):
            result = await agent.review(state_with_code)
            assert result["passed"] is False
            assert result["status"] == "review_unavailable"
            assert "API timeout" in result["note"]

    @pytest.mark.asyncio
    async def test_review_success(self, agent, state_with_code):
        mock_result = {"score": 85, "passed": True, "issues": [], "suggestions": ["use events"]}
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.review(state_with_code)
            assert result["passed"] is True
            assert result["score"] == 85

    @pytest.mark.asyncio
    async def test_execute_parse_error_phase(self, agent, state_with_code):
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value={"parse_error": True}):
            result = await agent.execute(state_with_code)
            assert result["current_phase"] == "code_review_unavailable"

    @pytest.mark.asyncio
    async def test_execute_exception_phase(self, agent, state_with_code):
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await agent.execute(state_with_code)
            assert result["current_phase"] == "code_review_unavailable"

    @pytest.mark.asyncio
    async def test_execute_success_phase(self, agent, state_with_code):
        mock_result = {"score": 90, "passed": True, "issues": []}
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.execute(state_with_code)
            assert result["current_phase"] == "code_review_passed"
