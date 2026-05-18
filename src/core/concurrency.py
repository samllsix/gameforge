"""GameForge - 并发管理模块

提供信号量、任务队列、并发控制等基础设施，解决高并发场景下的资源竞争问题。
"""

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import structlog

logger = structlog.get_logger()


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QueuedTask:
    """队列任务"""

    task_id: str
    task_type: str
    payload: Dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    priority: int = 0  # 优先级，数值越小优先级越高


class ConcurrencyManager:
    """并发管理器 — 控制系统整体并发度"""

    _instance: Optional["ConcurrencyManager"] = None
    _lock = asyncio.Lock()

    def __init__(
        self,
        max_concurrent_workflows: int = 5,
        max_concurrent_llm_calls: int = 10,
        max_queue_size: int = 100,
    ):
        # 信号量：限制同时运行的工作流数量
        self.workflow_semaphore = asyncio.Semaphore(max_concurrent_workflows)
        # 信号量：限制同时进行的LLM调用数量
        self.llm_semaphore = asyncio.Semaphore(max_concurrent_llm_calls)
        # 任务队列
        self.task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        # 活跃任务跟踪
        self.active_tasks: Dict[str, QueuedTask] = {}
        # 统计信息
        self.stats = {
            "total_submitted": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_rejected": 0,
        }
        # 队列处理器是否运行中
        self._processor_running = False
        self._processor_task: Optional[asyncio.Task] = None

    @classmethod
    async def get_instance(cls, **kwargs) -> "ConcurrencyManager":
        """获取全局单例"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    def _persist_task(self, task: QueuedTask):
        """持久化任务状态到数据库（非阻塞）"""
        try:
            from src.db.session import _engine
            if _engine is None:
                return
            from src.db.session import get_db
            from src.db.models import TaskRecord
            db = get_db()
            try:
                record = db.query(TaskRecord).filter(TaskRecord.id == task.task_id).first()
                if not record:
                    record = TaskRecord(
                        id=task.task_id,
                        task_type=task.task_type,
                        payload=task.payload,
                        priority=task.priority,
                    )
                    db.add(record)
                record.status = task.status.value
                record.result = task.result
                record.error = task.error
                record.started_at = datetime.fromtimestamp(task.started_at) if task.started_at else None
                record.completed_at = datetime.fromtimestamp(task.completed_at) if task.completed_at else None
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    async def submit_task(
        self,
        task_type: str,
        payload: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        priority: int = 0,
    ) -> str:
        """提交任务到队列

        Args:
            task_type: 任务类型（如 'workflow', 'plan', 'generate'）
            payload: 任务数据
            handler: 异步处理函数
            priority: 优先级（0=最高）

        Returns:
            任务ID
        """
        task_id = str(uuid.uuid4())[:8]
        queued_task = QueuedTask(
            task_id=task_id,
            task_type=task_type,
            payload=payload,
            priority=priority,
        )
        self.active_tasks[task_id] = queued_task
        self.stats["total_submitted"] += 1
        self._persist_task(queued_task)

        # 放入优先级队列
        await self.task_queue.put((priority, time.time(), task_id, handler))
        logger.info(
            "task_submitted",
            task_id=task_id,
            task_type=task_type,
            queue_size=self.task_queue.qsize(),
        )

        # 确保队列处理器在运行
        await self._ensure_processor()
        return task_id

    async def _ensure_processor(self):
        """确保队列处理器正在运行"""
        if not self._processor_running:
            self._processor_running = True
            self._processor_task = asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        """队列处理循环"""
        try:
            while not self.task_queue.empty():
                priority, submit_time, task_id, handler = (
                    await self.task_queue.get()
                )

                task = self.active_tasks.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    continue

                # 使用工作流信号量控制并发
                async with self.workflow_semaphore:
                    task.status = TaskStatus.RUNNING
                    task.started_at = time.time()
                    self._persist_task(task)
                    logger.info("task_started", task_id=task_id)

                    try:
                        result = await handler(task.payload)
                        task.status = TaskStatus.COMPLETED
                        task.result = result
                        self.stats["total_completed"] += 1
                        logger.info(
                            "task_completed",
                            task_id=task_id,
                            duration=time.time() - task.started_at,
                        )
                    except Exception as e:
                        task.status = TaskStatus.FAILED
                        task.error = str(e)
                        self.stats["total_failed"] += 1
                        logger.error("task_failed", task_id=task_id, error=str(e))
                    finally:
                        task.completed_at = time.time()
                        self._persist_task(task)
        finally:
            self._processor_running = False

    async def get_task_status(self, task_id: str) -> Optional[QueuedTask]:
        """获取任务状态"""
        return self.active_tasks.get(task_id)

    async def wait_for_task(
        self, task_id: str, timeout: float = 300.0
    ) -> Optional[QueuedTask]:
        """等待任务完成

        Args:
            task_id: 任务ID
            timeout: 超时时间（秒）

        Returns:
            任务信息，超时返回None
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.active_tasks.get(task_id)
            if task and task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return task
            await asyncio.sleep(0.5)
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取并发统计信息"""
        active_count = sum(
            1
            for t in self.active_tasks.values()
            if t.status == TaskStatus.RUNNING
        )
        pending_count = sum(
            1
            for t in self.active_tasks.values()
            if t.status == TaskStatus.PENDING
        )
        return {
            **self.stats,
            "active_tasks": active_count,
            "pending_tasks": pending_count,
            "queue_size": self.task_queue.qsize(),
        }

    async def cleanup_stale_tasks(self, max_age_seconds: float = 3600):
        """清理过期任务记录"""
        now = time.time()
        stale_ids = [
            tid
            for tid, task in self.active_tasks.items()
            if task.completed_at and (now - task.completed_at) > max_age_seconds
        ]
        for tid in stale_ids:
            del self.active_tasks[tid]
        return len(stale_ids)


class RateLimiter:
    """基于滑动窗口的异步安全速率限制器"""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    async def is_allowed(self, client_id: str) -> bool:
        """检查请求是否允许

        Args:
            client_id: 客户端标识

        Returns:
            是否允许
        """
        async with self._lock:
            now = time.time()
            window_start = now - self.window_seconds

            if client_id not in self._requests:
                self._requests[client_id] = []

            # 清理过期记录
            self._requests[client_id] = [
                t for t in self._requests[client_id] if t > window_start
            ]

            if len(self._requests[client_id]) >= self.max_requests:
                return False

            self._requests[client_id].append(now)
            return True

    async def get_remaining(self, client_id: str) -> int:
        """获取剩余请求次数"""
        async with self._lock:
            now = time.time()
            window_start = now - self.window_seconds
            if client_id not in self._requests:
                return self.max_requests
            valid = [t for t in self._requests[client_id] if t > window_start]
            return max(0, self.max_requests - len(valid))

    async def cleanup_stale_entries(self, max_age_seconds: float = 600) -> int:
        """清理过期客户端记录"""
        async with self._lock:
            now = time.time()
            stale_keys = [
                k
                for k, v in self._requests.items()
                if not v or (now - v[-1]) > max_age_seconds
            ]
            for k in stale_keys:
                del self._requests[k]
            return len(stale_keys)
