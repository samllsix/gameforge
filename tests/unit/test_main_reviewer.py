import pytest

from src.agents.main_reviewer import MainReviewerAgent


@pytest.fixture
def reviewer():
    return MainReviewerAgent({"agents": {"main_reviewer": {}}})


def test_review_runs_twice_and_returns_design_report(reviewer):
    state = {
        "code_generated": {"scripts/player.gd": "extends CharacterBody2D\nfunc _ready():\n\tpass\n"},
        "game_design_model": {
            "genre": "platformer",
            "core_loop": "move and jump",
            "player_actions": ["move", "jump"],
            "win_conditions": ["reach goal"],
            "fail_conditions": ["fall"],
            "entities": [{"name": "Player", "role": "player"}, {"name": "Ground", "role": "environment"}],
        },
        "scene_description": {},
    }

    first = reviewer.review(state)
    second = reviewer.rereview(state, first)
    design = reviewer.review_game_design(state)

    assert first["warnings"]
    assert second["passed"] is False
    assert design["passed"] is True


@pytest.mark.asyncio
async def test_execute_combines_code_and_design_results(reviewer):
    result = await reviewer.execute({
        "code_generated": {"scripts/player.gd": "extends CharacterBody2D\n"},
        "game_design_model": {
            "genre": "platformer", "core_loop": "jump", "player_actions": ["jump"],
            "win_conditions": ["goal"], "fail_conditions": ["fall"],
            "entities": [{"name": "Player", "role": "player"}, {"name": "Ground", "role": "ground"}],
        },
        "scene_description": {},
    })
    assert "first_review" in result["main_review_result"]
    assert "rereview" in result["main_review_result"]
    assert result["design_review_result"]["checked"]
