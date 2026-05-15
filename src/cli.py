"""GameForge - 命令行接口模块

提供命令行工具来运行GameForge平台。
"""

import asyncio
import click
import yaml
from pathlib import Path
from typing import Dict, Any

from src.core.graph.workflow import create_workflow


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
    config = ctx.obj["config"]

    # 读取需求文档
    with open(input_file, "r", encoding="utf-8") as f:
        requirements = f.read()

    click.echo(f"正在处理需求文档: {input_file}")

    # 创建工作流
    workflow = create_workflow(config)

    # 运行工作流
    result = asyncio.run(workflow.run({
        "project_context": {
            "engine": "unity",
            "project_name": "GameForge Project",
        },
        "requirements": requirements,
    }))

    # 输出结果
    click.echo(f"代码生成完成!")
    click.echo(f"生成文件数: {len(result.get('code_generated', {}))}")

    # 保存生成的代码
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for file_path, content in result.get("code_generated", {}).items():
        full_path = output_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        click.echo(f"  - {full_path}")


@cli.command()
@click.pass_context
def workflow(ctx):
    """运行完整的游戏开发工作流"""
    config = ctx.obj["config"]
    click.echo("正在启动GameForge工作流...")

    # 创建工作流
    workflow = create_workflow(config)

    # 示例需求
    requirements = """
    创建一个2D平台跳跃游戏：
    1. 玩家角色可以左右移动和跳跃
    2. 有平台和障碍物
    3. 碰撞检测系统
    4. 计分系统
    5. 游戏结束和重新开始功能
    """

    # 运行工作流
    result = asyncio.run(workflow.run({
        "project_context": {
            "engine": "unity",
            "project_name": "Platformer Game",
        },
        "requirements": requirements,
    }))

    click.echo("工作流执行完成!")
    click.echo(f"任务完成数: {len(result.get('task_plan', []))}")
    click.echo(f"生成文件数: {len(result.get('code_generated', {}))}")
    click.echo(f"修复次数: {len(result.get('fix_history', []))}")


@cli.command()
@click.pass_context
def status(ctx):
    """显示系统状态"""
    config = ctx.obj["config"]
    click.echo("GameForge 系统状态:")
    click.echo(f"  - 版本: {config.get('app', {}).get('version', '0.1.0')}")
    click.echo(f"  - 环境: {config.get('app', {}).get('environment', 'development')}")
    click.echo(f"  - 默认模型: {config.get('llm', {}).get('default_model', 'N/A')}")


def main():
    """主入口函数"""
    cli(obj={})


if __name__ == "__main__":
    main()
