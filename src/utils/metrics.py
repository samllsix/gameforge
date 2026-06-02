"""GameForge - Prometheus指标模块

暴露应用指标供Prometheus抓取：
- LLM调用指标（请求数、延迟、错误率、token用量）
- 工作流指标（任务完成数、生成文件数、修复轮数）
- 系统指标（并发数、缓存命中率）
"""

import time
import logging
from typing import Optional

logger = logging.getLogger("GameForge.metrics")

# 使用 prometheus_client 库（如果安装了的话）
# 没安装时降级为空操作，不影响主流程
try:
    from prometheus_client import (
        Counter, Histogram, Gauge, Info,
        CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus-client未安装，指标功能禁用。运行: pip install prometheus-client")

# ========== 指标定义 ==========

if PROMETHEUS_AVAILABLE:
    # 创建自定义 registry（避免默认的进程指标污染）
    registry = CollectorRegistry()

    # LLM 调用指标
    llm_requests_total = Counter(
        "gameforge_llm_requests_total",
        "LLM调用总次数",
        ["provider", "model", "method", "status"],
        registry=registry,
    )
    llm_request_duration = Histogram(
        "gameforge_llm_request_duration_seconds",
        "LLM调用延迟（秒）",
        ["provider", "model"],
        buckets=[0.5, 1, 2, 5, 10, 20, 30, 60],
        registry=registry,
    )
    llm_cache_hits = Counter(
        "gameforge_llm_cache_hits_total",
        "LLM缓存命中次数",
        ["provider", "model"],
        registry=registry,
    )

    # 工作流指标
    workflow_runs_total = Counter(
        "gameforge_workflow_runs_total",
        "工作流执行总次数",
        ["status"],
        registry=registry,
    )
    workflow_duration = Histogram(
        "gameforge_workflow_duration_seconds",
        "工作流执行时长（秒）",
        buckets=[10, 30, 60, 120, 300, 600],
        registry=registry,
    )
    tasks_completed_total = Counter(
        "gameforge_tasks_completed_total",
        "任务完成总次数",
        ["task_type"],
        registry=registry,
    )
    files_generated_total = Counter(
        "gameforge_files_generated_total",
        "生成文件总次数",
        ["file_type"],
        registry=registry,
    )
    fix_attempts_total = Counter(
        "gameforge_fix_attempts_total",
        "修复尝试总次数",
        ["result"],
        registry=registry,
    )

    # 系统指标
    active_workflows = Gauge(
        "gameforge_active_workflows",
        "当前活跃工作流数",
        registry=registry,
    )
    vector_store_size = Gauge(
        "gameforge_vector_store_size",
        "向量库存储数量",
        registry=registry,
    )

    # 应用信息
    app_info = Info(
        "gameforge_app",
        "应用信息",
        registry=registry,
    )
    # 初始化应用信息
    app_info.info({
        "version": "2.1",
        "name": "GameForge",
        "description": "AI游戏研发全流程Agent协作平台",
    })


# ========== 指标记录函数 ==========

def record_llm_call(provider: str, model: str, method: str, duration: float, success: bool, cached: bool = False):
    """记录LLM调用指标

    Args:
        provider: 提供商（mimo/deepseek/zhipu/kimi）
        model: 模型名
        method: 调用方法（chat/chat_json）
        duration: 调用时长（秒）
        success: 是否成功
        cached: 是否命中缓存
    """
    if not PROMETHEUS_AVAILABLE:
        return

    status = "success" if success else "error"
    llm_requests_total.labels(provider=provider, model=model, method=method, status=status).inc()
    llm_request_duration.labels(provider=provider, model=model).observe(duration)

    if cached:
        llm_cache_hits.labels(provider=provider, model=model).inc()


def record_workflow_run(success: bool, duration: float):
    """记录工作流执行"""
    if not PROMETHEUS_AVAILABLE:
        return
    status = "success" if success else "error"
    workflow_runs_total.labels(status=status).inc()
    workflow_duration.observe(duration)


def record_task_completed(task_type: str):
    """记录任务完成"""
    if not PROMETHEUS_AVAILABLE:
        return
    tasks_completed_total.labels(task_type=task_type).inc()


def record_file_generated(file_type: str):
    """记录文件生成"""
    if not PROMETHEUS_AVAILABLE:
        return
    files_generated_total.labels(file_type=file_type).inc()


def record_fix_attempt(success: bool):
    """记录修复尝试"""
    if not PROMETHEUS_AVAILABLE:
        return
    result = "success" if success else "failed"
    fix_attempts_total.labels(result=result).inc()


def set_active_workflows(count: int):
    """设置活跃工作流数"""
    if not PROMETHEUS_AVAILABLE:
        return
    active_workflows.set(count)


def set_vector_store_size(count: int):
    """设置向量库存储数量"""
    if not PROMETHEUS_AVAILABLE:
        return
    vector_store_size.set(count)


def get_metrics() -> Optional[bytes]:
    """获取Prometheus指标文本（供 /metrics 端点使用）

    Returns:
        指标文本字节，Prometheus不可用时返回None
    """
    if not PROMETHEUS_AVAILABLE:
        return None
    return generate_latest(registry)


def get_content_type() -> str:
    """获取指标响应的Content-Type"""
    if not PROMETHEUS_AVAILABLE:
        return "text/plain"
    return CONTENT_TYPE_LATEST


# ========== 计时上下文管理器 ==========

class MetricsTimer:
    """计时器，用于记录操作耗时"""

    def __init__(self, provider: str = "", model: str = "", method: str = ""):
        self.provider = provider
        self.model = model
        self.method = method
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        success = exc_type is None
        record_llm_call(self.provider, self.model, self.method, duration, success)
        return False
