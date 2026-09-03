"""Game DSL tests."""

import pytest

from src.core.dsl import GameDSL


SIMPLE_SPEC = {
    "game": {
        "genre": "tower_defense",
        "camera": "2D_top_down",
        "difficulty": "medium",
    },
    "player": {
        "hp": 100,
        "actions": ["plant", "collect"],
        "resources": ["sun"],
    },
    "enemy": {
        "types": ["zombie", "cone_zombie"],
        "wave_system": True,
    },
    "mechanics": ["resource_generation", "wave_attack"],
    "assets": ["plant", "zombie", "map"],
    "scenes": ["main_menu", "level_1"],
}


def test_from_spec_produces_yaml():
    yaml_text = GameDSL.from_spec(SIMPLE_SPEC)
    assert isinstance(yaml_text, str)
    assert "genre: tower_defense" in yaml_text
    assert "camera: 2D_top_down" in yaml_text
    assert "hp: 100" in yaml_text
    assert "- zombie" in yaml_text
    assert "wave_system: true" in yaml_text


def test_roundtrip_spec_preserves_values():
    yaml_text = GameDSL.from_spec(SIMPLE_SPEC)
    parsed = GameDSL.to_spec(yaml_text)
    assert parsed["game"]["genre"] == "tower_defense"
    assert parsed["game"]["camera"] == "2D_top_down"
    assert parsed["player"]["hp"] == 100
    assert parsed["enemy"]["wave_system"] is True
    assert parsed["mechanics"] == ["resource_generation", "wave_attack"]
    assert parsed["scenes"] == ["main_menu", "level_1"]


def test_validate_accepts_valid_spec():
    assert GameDSL.validate(SIMPLE_SPEC) is True
    assert GameDSL.validate({"game": {"genre": "casual"}}) is True
    assert GameDSL.validate({"genre": "casual"}) is True


def test_validate_rejects_invalid_spec():
    assert GameDSL.validate({}) is False
    assert GameDSL.validate(None) is False
    assert GameDSL.validate("not a dict") is False


def test_merge_overrides_nested_values():
    base = {
        "game": {"genre": "casual", "camera": "2D_side", "difficulty": "easy"},
        "player": {"hp": 100},
    }
    override = {
        "game": {"genre": "tower_defense", "difficulty": "hard"},
        "player": {"hp": 150},
    }
    merged = GameDSL.merge(base, override)
    assert merged["game"]["genre"] == "tower_defense"
    assert merged["game"]["camera"] == "2D_side"
    assert merged["game"]["difficulty"] == "hard"
    assert merged["player"]["hp"] == 150
