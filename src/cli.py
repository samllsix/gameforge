"""GameForge - 命令行接口模块

提供命令行工具来运行GameForge平台。
"""

import asyncio
import click
import yaml
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv
load_dotenv()

from src.core.graph.workflow import create_workflow
from src.utils.logger import get_logger, reset_logger


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """加载配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@click.group()
@click.option("--config", "-c", default="config/config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config):
    """GameForge - 游戏研发全流程AI Agent协作平台"""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@cli.command()
@click.option("--input", "-i", "input_file", required=True, help="需求文档路径")
@click.option("--output", "-o", "output_dir", default="output", help="输出目录")
@click.pass_context
def generate(ctx, input_file, output_dir):
    """根据需求文档生成游戏代码

    Args:
        input_file: 需求文档路径
        output_dir: 输出目录
    """
    # 重置日志并创建新实例
    reset_logger()
    logger = get_logger(prefix="generate")

    config = ctx.obj["config"]

    # 读取需求文档
    with open(input_file, "r", encoding="utf-8") as f:
        requirements = f.read()

    logger.section("GameForge 代码生成任务")
    logger.result("需求文档", input_file)
    logger.result("输出目录", output_dir)
    logger.result("需求内容", requirements[:100] + "..." if len(requirements) > 100 else requirements)

    # 创建工作流
    logger.subsection("初始化工作流")
    workflow = create_workflow(config)
    logger.success("工作流初始化完成")

    # 运行工作流
    logger.subsection("执行工作流")
    result = asyncio.run(workflow.run({
        "project_context": {
            "engine": "unity",
            "project_name": "GameForge Project",
            "requirements": requirements,
        },
    }))

    # 输出结果
    logger.section("执行结果")
    logger.result("任务完成数", str(len(result.get("task_plan", []))))
    logger.result("生成文件数", str(len(result.get("code_generated", {}))))

    # 保存生成的代码
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.subsection("生成的文件")
    for file_path, content in result.get("code_generated", {}).items():
        full_path = output_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        logger.result("文件", str(full_path))

    # 运行评测
    logger.subsection("代码评测")
    try:
        from scripts.evaluate import CodeEvaluator
        evaluator = CodeEvaluator(output_dir)
        eval_results = evaluator.evaluate()
        logger.result("评测状态", "完成")
    except Exception as e:
        logger.warning(f"评测脚本执行失败: {e}")

    logger.section("任务完成")
    logger.success(f"日志已保存: {logger.get_log_file()}")
    click.echo(f"\n日志文件: {logger.get_log_file()}")


@cli.command()
@click.pass_context
def workflow(ctx):
    """运行完整的游戏开发工作流"""
    # 重置日志并创建新实例
    reset_logger()
    logger = get_logger(prefix="workflow")

    config = ctx.obj["config"]

    logger.section("GameForge 工作流任务")

    # 创建工作流
    logger.subsection("初始化工作流")
    workflow = create_workflow(config)
    logger.success("工作流初始化完成")

    # 示例需求
    requirements = """
    创建一个2D平台跳跃游戏：
    1. 玩家角色可以左右移动和跳跃
    2. 有平台和障碍物
    3. 碰撞检测系统
    4. 计分系统
    5. 游戏结束和重新开始功能
    """

    logger.result("需求内容", requirements.strip())

    # 运行工作流
    logger.subsection("执行工作流")
    result = asyncio.run(workflow.run({
        "project_context": {
            "engine": "unity",
            "project_name": "Platformer Game",
            "requirements": requirements,
        },
    }))

    # 输出结果
    logger.section("执行结果")
    logger.result("任务完成数", str(len(result.get("task_plan", []))))
    logger.result("生成文件数", str(len(result.get("code_generated", {}))))
    logger.result("修复次数", str(len(result.get("fix_history", []))))

    # 生成的文件
    logger.subsection("生成的文件")
    for file_path in result.get("code_generated", {}).keys():
        logger.result("文件", file_path)

    logger.section("任务完成")
    logger.success(f"日志已保存: {logger.get_log_file()}")
    click.echo(f"\n日志文件: {logger.get_log_file()}")


@cli.command()
@click.pass_context
def status(ctx):
    """显示系统状态"""
    # 重置日志并创建新实例
    reset_logger()
    logger = get_logger(prefix="status")

    config = ctx.obj["config"]

    logger.section("GameForge 系统状态")
    logger.result("版本", config.get("app", {}).get("version", "0.1.0"))
    logger.result("环境", config.get("app", {}).get("environment", "development"))
    logger.result("默认模型", config.get("llm", {}).get("default_model", "N/A"))

    logger.section("状态检查完成")
    logger.success(f"日志已保存: {logger.get_log_file()}")
    click.echo(f"\n日志文件: {logger.get_log_file()}")


def main():
    """主入口函数"""
    cli(obj={})


if __name__ == "__main__":
    main()
