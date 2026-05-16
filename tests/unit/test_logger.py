"""测试 Logger 模块"""

import pytest
from pathlib import Path
from src.utils.logger import GameForgeLogger, get_logger, reset_logger


class TestGameForgeLogger:
    def test_init_creates_log_dir(self, temp_dir):
        log_dir = temp_dir / "test_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        assert log_dir.exists()
        assert logger.log_file.exists()

    def test_init_creates_log_file(self, temp_dir):
        log_dir = temp_dir / "logs_test"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="unit")
        assert logger.log_file.exists()
        assert "unit_" in str(logger.log_file)

    def test_info_writes_to_file(self, temp_dir):
        log_dir = temp_dir / "info_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.info("Hello World")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "Hello World" in content

    def test_debug_writes_to_file(self, temp_dir):
        log_dir = temp_dir / "debug_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.debug("Debug message")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "Debug message" in content

    def test_warning_writes_to_file(self, temp_dir):
        log_dir = temp_dir / "warn_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.warning("Warning message")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "Warning message" in content
        assert "WARNING" in content

    def test_error_writes_to_file(self, temp_dir):
        log_dir = temp_dir / "err_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.error("Error message")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "Error message" in content
        assert "ERROR" in content

    def test_section_format(self, temp_dir):
        log_dir = temp_dir / "section_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.section("My Section")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "=" * 60 in content
        assert "My Section" in content

    def test_subsection_format(self, temp_dir):
        log_dir = temp_dir / "subsection_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.subsection("Sub Section")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "--- Sub Section ---" in content

    def test_result_format(self, temp_dir):
        log_dir = temp_dir / "result_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.result("Key", "Value")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "Key: Value" in content

    def test_success_format(self, temp_dir):
        log_dir = temp_dir / "success_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.success("Done!")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "[SUCCESS]" in content
        assert "Done!" in content

    def test_failure_format(self, temp_dir):
        log_dir = temp_dir / "failure_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.failure("Failed!")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "[FAILURE]" in content
        assert "Failed!" in content

    def test_get_log_file(self, temp_dir):
        log_dir = temp_dir / "getfile_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        assert isinstance(logger.get_log_file(), Path)

    def test_multiple_log_lines(self, temp_dir):
        log_dir = temp_dir / "multi_logs"
        logger = GameForgeLogger(log_dir=str(log_dir), prefix="test")
        logger.section("Start")
        logger.subsection("Step 1")
        logger.result("A", "1")
        logger.result("B", "2")
        logger.success("All done")
        
        content = logger.log_file.read_text(encoding="utf-8")
        assert "Start" in content
        assert "Step 1" in content
        assert "A: 1" in content
        assert "B: 2" in content
        assert "All done" in content


class TestGlobalLogger:
    def test_get_logger_creates_instance(self, temp_dir):
        reset_logger()
        log_dir = temp_dir / "global_logs"
        logger = get_logger(log_dir=str(log_dir), prefix="global")
        assert isinstance(logger, GameForgeLogger)
        assert logger.log_file.exists()

    def test_get_logger_returns_same_instance(self, temp_dir):
        reset_logger()
        log_dir = temp_dir / "same_logs"
        logger1 = get_logger(log_dir=str(log_dir), prefix="a")
        logger2 = get_logger()
        assert logger1 is logger2

    def test_reset_logger(self, temp_dir):
        reset_logger()
        log_dir = temp_dir / "reset_logs"
        logger1 = get_logger(log_dir=str(log_dir), prefix="a")
        log_file1 = logger1.get_log_file()
        reset_logger()
        log_dir2 = temp_dir / "reset_logs2"
        logger2 = get_logger(log_dir=str(log_dir2), prefix="b")
        assert logger1 is not logger2
        assert logger2.get_log_file() != log_file1
