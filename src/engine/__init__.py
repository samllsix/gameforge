"""GameForge - Godot 游戏引擎集成模块

提供 Godot 引擎的编译、导入、场景构建等功能。
支持 Godot 3.x 和 4.x 版本。
"""

from typing import Dict, Any, Optional


def get_engine_editor(engine: str, config: Dict[str, Any]):
    """获取引擎编辑器实例

    Args:
        engine: 引擎类型 (godot)
        config: 配置字典

    Returns:
        引擎编辑器实例
    """
    if engine == "godot":
        from src.engine.godot import GodotEditor
        return GodotEditor(config)
    else:
        raise ValueError(f"不支持的引擎类型: {engine}，当前仅支持 godot")


def get_compiler(engine: str, config: Dict[str, Any]):
    """获取编译器实例

    Args:
        engine: 引擎类型
        config: 配置字典

    Returns:
        编译器实例
    """
    from src.engine.godot import GodotCompiler
    return GodotCompiler(config)


def get_sandbox(config: Dict[str, Any]):
    """获取沙箱执行器实例

    Args:
        config: 配置字典

    Returns:
        沙箱执行器实例
    """
    from src.engine.sandbox import SandboxExecutor
    return SandboxExecutor(config)


def create_editor(engine_type: str, config: Dict[str, Any]):
    """创建引擎编辑器实例（别名）

    Args:
        engine_type: 引擎类型 (godot)
        config: 配置字典

    Returns:
        引擎编辑器实例
    """
    return get_engine_editor(engine_type, config)
