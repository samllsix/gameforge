"""Tests for LLM JSON extraction helpers."""

from src.utils.json_extractor import extract_json, extract_json_strict


def test_extract_json_direct_object():
    assert extract_json('{"ok": true}') == {"ok": True}


def test_extract_json_from_lowercase_fence():
    text = 'LLM output:\n```json\n{"game_title": "Demo"}\n```'
    assert extract_json(text) == {"game_title": "Demo"}


def test_extract_json_from_uppercase_fence():
    text = '```JSON\n{"game_objects": []}\n```'
    assert extract_json(text) == {"game_objects": []}


def test_extract_json_from_embedded_object():
    text = 'prefix {"game_title": "Embedded", "genre": "platformer"} suffix'
    assert extract_json(text) == {"game_title": "Embedded", "genre": "platformer"}


def test_extract_json_returns_none_by_default_on_failure():
    assert extract_json("not json") is None


def test_extract_json_strict_returns_parse_error_on_failure():
    result = extract_json_strict("not json")
    assert result["parse_error"] is True
    assert result["raw_response"] == "not json"
