"""GameForge - 游戏引擎集成模块

提供Unity和Unreal引擎的编译、导入、执行等功能。
"""

from typing import Dict, Any, Optional


def get_engine_editor(engine: str, config: Dict[str, Any]):
    """获取引擎编辑器实例

    Args:
        engine: 引擎类型 (unity/unreal)
        config: 配置字典

    Returns:
        引擎编辑器实例
    """
    if engine == "unity":
        from src.engine.unity import UnityEditor
        return UnityEditor(config)
    elif engine == "unreal":
        from src.engine.unreal import UnrealEditor
        return UnrealEditor(config)
    else:
        raise ValueError(f"不支持的引擎类型: {engine}")


def get_compiler(engine: str, config: Dict[str, Any]):
    """获取编译器实例

    Args:
        engine: 引擎类型
        config: 配置字典

    Returns:
        编译器实例
    """
    from src.engine.compiler import CSharpCompiler
    return CSharpCompiler(config)


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
        engine_type: 引擎类型 (unity/unreal)
        config: 配置字典

    Returns:
        引擎编辑器实例
    """
    return get_engine_editor(engine_type, config)
