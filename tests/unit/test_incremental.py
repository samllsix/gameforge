"""Incremental generation tests."""

import pytest

from src.core.incremental import DependencyGraph, IncrementalGenerator


def test_dependency_graph_extracts_gd_deps():
    files = [
        {"path": "scripts/player.gd", "content": 'extends Node2D\n\nvar partner = preload("res://scripts/partner.gd")'},
        {"path": "scripts/partner.gd", "content": "extends Node2D"},
        {"path": "scripts/enemy.gd", "content": "extends Node2D"},
    ]
    graph = DependencyGraph.analyze(files)
    assert "scripts/player.gd" in graph
    assert "scripts/partner.gd" in graph["scripts/player.gd"]


def test_dependency_graph_extracts_tscn_deps():
    files = [
        {"path": "scenes/level.tscn", "content": '[ext_resource path="res://scripts/player.gd" type="Script" id="1"]'},
        {"path": "scripts/player.gd", "content": "extends Node2D"},
    ]
    graph = DependencyGraph.analyze(files)
    assert "scenes/level.tscn" in graph
    assert "scripts/player.gd" in graph["scenes/level.tscn"]


def test_compute_impact_includes_transitive_deps():
    dep_graph = {
        "scripts/a.gd": {"scripts/b.gd"},
        "scripts/b.gd": {"scripts/c.gd"},
        "scripts/c.gd": set(),
    }
    gen = IncrementalGenerator(dep_graph)
    impacted = gen.compute_impact(["scripts/a.gd"])
    assert impacted == {"scripts/a.gd", "scripts/b.gd", "scripts/c.gd"}


def test_filter_task_plan_keeps_impacted_tasks():
    task_plan = [
        {"name": "player", "files": ["scripts/player.gd"]},
        {"name": "ui", "files": ["scenes/ui.tscn"]},
        {"name": "config", "files": []},
    ]
    gen = IncrementalGenerator()
    filtered = gen.filter_task_plan(task_plan, {"scripts/player.gd"})
    assert [t["name"] for t in filtered] == ["player", "config"]


def test_filter_code_generated_keeps_impacted_files():
    code_generated = {
        "scripts/player.gd": "extends Node2D",
        "scripts/enemy.gd": "extends Node2D",
    }
    gen = IncrementalGenerator()
    filtered = gen.filter_code_generated(code_generated, {"scripts/player.gd"})
    assert list(filtered.keys()) == ["scripts/player.gd"]


def test_should_regenerate_detects_changes():
    gen = IncrementalGenerator()
    old_state = {
        "game_design_model": {
            "entities": [{"name": "Player"}],
            "scenes": ["main_menu"],
        }
    }
    new_state = {
        "game_design_model": {
            "entities": [{"name": "Player"}, {"name": "Enemy"}],
            "scenes": ["main_menu", "level1"],
        }
    }
    needed, changed = gen.should_regenerate(old_state, new_state)
    assert needed is True
    assert "scripts/enemy.gd" in changed
    assert "scenes/level1.tscn" in changed
