"""GameForge - 日志管理模块

提供统一的日志记录功能，支持控制台输出和文件保存。
"""

import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


class GameForgeLogger:
    """GameForge日志管理器"""

    def __init__(self, log_dir: str = "logs", prefix: str = "gameforge"):
        """初始化日志管理器

        Args:
            log_dir: 日志目录
            prefix: 日志文件前缀
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.prefix = prefix
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{prefix}_{self.timestamp}.log"

        # 创建logger
        self.logger = logging.getLogger("GameForge")
        self.logger.setLevel(logging.DEBUG)

        # 清除已有的handler
        self.logger.handlers.clear()

        # 添加文件handler
        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

        # 添加控制台handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        self.logger.info(f"日志文件: {self.log_file}")

    def info(self, message: str):
        """记录信息日志"""
        self.logger.info(message)

    def debug(self, message: str):
        """记录调试日志"""
        self.logger.debug(message)

    def warning(self, message: str):
        """记录警告日志"""
        self.logger.warning(message)

    def error(self, message: str):
        """记录错误日志"""
        self.logger.error(message)

    def section(self, title: str):
        """记录分节标题"""
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info(title)
        self.logger.info("=" * 60)

    def subsection(self, title: str):
        """记录子标题"""
        self.logger.info("")
        self.logger.info(f"--- {title} ---")

    def result(self, key: str, value: str):
        """记录结果"""
        self.logger.info(f"  {key}: {value}")

    def success(self, message: str):
        """记录成功信息"""
        self.logger.info(f"[SUCCESS] {message}")

    def failure(self, message: str):
        """记录失败信息"""
        self.logger.error(f"[FAILURE] {message}")

    def get_log_file(self) -> Path:
        """获取日志文件路径"""
        return self.log_file


# 全局日志实例
_logger: Optional[GameForgeLogger] = None


def get_logger(log_dir: str = "logs", prefix: str = "gameforge") -> GameForgeLogger:
    """获取全局日志实例

    Args:
        log_dir: 日志目录
        prefix: 日志文件前缀

    Returns:
        日志实例
    """
    global _logger
    if _logger is None:
        _logger = GameForgeLogger(log_dir, prefix)
    return _logger


def reset_logger():
    """重置日志实例"""
    global _logger
    _logger = None
