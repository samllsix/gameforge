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


@cli.command()
@click.option("--input", "-i", "input_dir", required=True, help="代码目录路径")
@click.option("--output", "-o", "output_dir", default=None, help="输出目录（默认覆盖原文件）")
@click.pass_context
def refactor(ctx, input_dir, output_dir):
    """重构代码目录中的C#文件"""
    reset_logger()
    logger = get_logger(prefix="refactor")

    config = ctx.obj["config"]

    logger.section("GameForge 代码重构")
    logger.result("输入目录", input_dir)

    from src.agents.refactor import RefactorAgent
    from src.core.tools import list_files, read_file, write_file

    agent = RefactorAgent(config)

    cs_files = list_files(input_dir, [".cs"])
    logger.result("找到文件", str(len(cs_files)))

    if not cs_files:
        logger.warning("未找到C#文件")
        return

    refactored_count = 0
    for file_path in cs_files:
        content = read_file(file_path)
        if not content:
            continue

        logger.subsection(f"分析: {file_path}")
        quality = agent.analyze_code_quality(content)
        logger.result("质量分数", str(quality["score"]))

        if quality["score"] < 70:
            logger.result("问题", ", ".join(quality["issues"][:3]))

    logger.section("重构完成")
    logger.success(f"日志已保存: {logger.get_log_file()}")


@cli.command()
@click.option("--project", "-p", default=None, help="Unity项目路径")
@click.option("--action", "-a", type=click.Choice(["compile", "refresh", "import"]), default="compile", help="操作类型")
@click.option("--files", "-f", default=None, help="要导入的文件目录")
@click.pass_context
def unity(ctx, project, action, files):
    """Unity编辑器操作"""
    reset_logger()
    logger = get_logger(prefix="unity")

    config = ctx.obj["config"]

    logger.section("GameForge Unity操作")

    from src.engine.unity import UnityEditor

    unity_config = config.get("unity", {})
    if project:
        unity_config["unity_project_path"] = project

    editor = UnityEditor(unity_config)

    is_valid, msg = editor.validate()
    if not is_valid:
        logger.failure(f"验证失败: {msg}")
        return

    logger.result("项目路径", editor.project_path)

    if action == "compile":
        logger.subsection("编译项目")
        result = editor.compile_project()
        if result.success:
            logger.success(f"编译成功 ({result.compile_time:.1f}秒)")
        else:
            logger.failure(f"编译失败: {len(result.errors)}个错误")
            for err in result.errors[:5]:
                logger.result("错误", err)

    elif action == "refresh":
        logger.subsection("刷新资源")
        if editor.refresh_assets():
            logger.success("资源刷新成功")
        else:
            logger.failure("资源刷新失败")

    elif action == "import" and files:
        logger.subsection("导入文件")
        from src.core.tools import list_files, read_file
        file_list = list_files(files, [".cs"])
        import_files = {}
        for f in file_list:
            content = read_file(f)
            if content:
                rel_path = f.replace(files, "").lstrip("/\\")
                import_files[f"Assets/Scripts/{rel_path}"] = content

        result = editor.import_files(import_files)
        if result.success:
            logger.success(f"导入成功: {len(result.imported_files)}个文件")
        else:
            logger.failure(f"导入失败: {len(result.failed_files)}个文件")

    logger.section("操作完成")


@cli.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", "-p", default=8000, type=int, help="监听端口")
@click.option("--workers", "-w", default=1, type=int, help="工作进程数")
@click.pass_context
def serve(ctx, host, port, workers):
    """启动API服务器（支持高并发）"""
    from src.api.main import start_server

    click.echo(f"启动GameForge API服务器...")
    click.echo(f"  地址: {host}:{port}")
    click.echo(f"  进程数: {workers}")
    click.echo(f"  并发限制: 20请求/进程")
    click.echo(f"  速率限制: 60请求/分钟/IP")
    click.echo()
    start_server(host=host, port=port, workers=workers)


@cli.command()
@click.option("--project", "-p", default="default", help="项目名称")
@click.option("--report", "-r", is_flag=True, help="生成评测报告")
@click.pass_context
def eval(ctx, project, report):
    """运行代码评测"""
    reset_logger()
    logger = get_logger(prefix="eval")

    logger.section("GameForge 代码评测")

    from src.eval.metrics import run_evaluation, CodeQualityMetrics
    from src.core.tools import list_files, read_file

    output_dir = "output"
    cs_files = list_files(output_dir, [".cs"])

    if not cs_files:
        logger.warning("未找到代码文件，请先运行 generate 命令")
        return

    logger.result("找到文件", str(len(cs_files)))

    code_files = {}
    for f in cs_files:
        content = read_file(f)
        if content:
            code_files[f] = content

    eval_report = run_evaluation(project, code_files=code_files)

    logger.subsection("评测结果")
    logger.result("总分", f"{eval_report.overall_score:.1f}")
    for metric in eval_report.metrics:
        logger.result(metric.name, f"{metric.score:.1f}")

    if report:
        report_path = eval_report.save()
        logger.result("报告已保存", report_path)

    logger.section("评测完成")


def main():
    """主入口函数"""
    cli(obj={})


if __name__ == "__main__":
    main()
