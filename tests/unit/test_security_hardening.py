"""Regression coverage for the hardened local execution boundary."""

import shutil

import pytest

from src.engine.sandbox import SandboxExecutor


def test_host_code_execution_is_refused_even_with_legacy_enabled_flag():
    result = SandboxExecutor({"sandbox": {"enabled": True}}).execute_python("print('unsafe')")

    assert result.success is False
    assert "isolated sandbox backend" in result.stderr


def test_temp_project_rejects_path_traversal():
    executor = SandboxExecutor({})

    with pytest.raises(ValueError, match="escapes temporary project"):
        executor.create_temp_project({"../outside.txt": "unsafe"})


def test_temp_project_accepts_project_relative_files():
    executor = SandboxExecutor({})
    project_dir = executor.create_temp_project({"scripts/main.py": "print('ok')"})
    try:
        with open(f"{project_dir}/scripts/main.py", encoding="utf-8") as source:
            assert source.read() == "print('ok')"
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
