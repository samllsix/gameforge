"""测试高并发模块"""

import pytest
import asyncio
import time
from typing import Dict
from src.core.concurrency import (
    ConcurrencyManager,
    RateLimiter,
    TaskStatus,
)


class TestRateLimiter:
    """速率限制器测试"""

    @pytest.mark.asyncio
    async def test_allows_within_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert await limiter.is_allowed("client1") is True

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            await limiter.is_allowed("client1")
        assert await limiter.is_allowed("client1") is False

    @pytest.mark.asyncio
    async def test_different_clients_independent(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        await limiter.is_allowed("client1")
        await limiter.is_allowed("client1")
        assert await limiter.is_allowed("client1") is False
        assert await limiter.is_allowed("client2") is True

    @pytest.mark.asyncio
    async def test_remaining_count(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        assert await limiter.get_remaining("client1") == 5
        await limiter.is_allowed("client1")
        assert await limiter.get_remaining("client1") == 4

    @pytest.mark.asyncio
    async def test_window_expiry(self):
        limiter = RateLimiter(max_requests=2, window_seconds=1)
        await limiter.is_allowed("client1")
        await limiter.is_allowed("client1")
        assert await limiter.is_allowed("client1") is False
        await asyncio.sleep(1.1)
        assert await limiter.is_allowed("client1") is True

    @pytest.mark.asyncio
    async def test_cleanup_stale_entries(self):
        limiter = RateLimiter(max_requests=5, window_seconds=1)
        await limiter.is_allowed("old_client")
        await asyncio.sleep(1.1)
        cleaned = await limiter.cleanup_stale_entries(max_age_seconds=1)
        assert cleaned == 1


class TestConcurrencyManager:
    """并发管理器测试"""

    @pytest.fixture
    async def manager(self):
        # 每个测试使用新实例
        ConcurrencyManager._instance = None
        mgr = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=3,
            max_concurrent_llm_calls=5,
            max_queue_size=50,
        )
        yield mgr
        ConcurrencyManager._instance = None

    @pytest.mark.asyncio
    async def test_submit_task(self, manager):
        async def handler(payload):
            return {"result": "ok"}

        task_id = await manager.submit_task("test", {"data": 1}, handler)
        assert task_id is not None
        assert len(task_id) == 8

    @pytest.mark.asyncio
    async def test_task_completion(self, manager):
        async def handler(payload):
            await asyncio.sleep(0.1)
            return {"sum": payload["a"] + payload["b"]}

        task_id = await manager.submit_task(
            "calc", {"a": 1, "b": 2}, handler
        )
        task = await manager.wait_for_task(task_id, timeout=5)
        assert task is not None
        assert task.status == TaskStatus.COMPLETED
        assert task.result["sum"] == 3

    @pytest.mark.asyncio
    async def test_task_failure(self, manager):
        async def handler(payload):
            raise ValueError("test error")

        task_id = await manager.submit_task("fail", {}, handler)
        task = await manager.wait_for_task(task_id, timeout=5)
        assert task.status == TaskStatus.FAILED
        assert "test error" in task.error

    @pytest.mark.asyncio
    async def test_concurrent_tasks(self, manager):
        results = []

        async def handler(payload):
            await asyncio.sleep(0.1)
            results.append(payload["id"])
            return {"id": payload["id"]}

        task_ids = []
        for i in range(5):
            tid = await manager.submit_task("batch", {"id": i}, handler)
            task_ids.append(tid)

        # 等待所有任务完成
        for tid in task_ids:
            task = await manager.wait_for_task(tid, timeout=10)
            assert task.status == TaskStatus.COMPLETED

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_stats(self, manager):
        async def handler(payload):
            return {}

        await manager.submit_task("test", {}, handler)
        await asyncio.sleep(0.5)
        stats = manager.get_stats()
        assert stats["total_submitted"] >= 1
        assert "active_tasks" in stats
        assert "pending_tasks" in stats

    @pytest.mark.asyncio
    async def test_nonexistent_task(self, manager):
        task = await manager.get_task_status("nonexistent")
        assert task is None

    @pytest.mark.asyncio
    async def test_priority_ordering(self, manager):
        execution_order = []

        async def handler(payload):
            execution_order.append(payload["priority"])
            return {}

        # 低优先级先提交
        await manager.submit_task("test", {"priority": 5}, handler, priority=5)
        await manager.submit_task("test", {"priority": 1}, handler, priority=1)
        await manager.submit_task("test", {"priority": 3}, handler, priority=3)

        await asyncio.sleep(2)
        # 优先级1应该先执行
        assert execution_order[0] == 1

    @pytest.mark.asyncio
    async def test_task_not_lost_when_submitted_during_processing(self, manager):
        """回归测试：在处理器运行期间提交的新任务不应丢失

        原竞态条件：_process_queue 在 gather 等待期间，新任务入队后，
        若处理器在 _processor_running=False 前退出，新任务会永久滞留队列。
        """
        completed_ids = []
        # 用事件阻塞第一个任务，让处理器处于"运行中但等待 gather"的状态
        first_task_started = asyncio.Event()
        release_first = asyncio.Event()

        async def blocking_handler(payload):
            first_task_started.set()
            await release_first.wait()
            completed_ids.append(payload["id"])
            return {"id": payload["id"]}

        async def quick_handler(payload):
            completed_ids.append(payload["id"])
            return {"id": payload["id"]}

        # 1. 提交第一个阻塞任务，让处理器进入"运行中"状态
        tid1 = await manager.submit_task(
            "block", {"id": 1}, blocking_handler
        )
        await first_task_started.wait()  # 确保第一个任务已开始

        # 2. 在处理器运行期间提交新任务（此时 _processor_running=True）
        tid2 = await manager.submit_task(
            "quick", {"id": 2}, quick_handler
        )

        # 3. 释放第一个任务，让处理器有机会退出
        release_first.set()

        # 4. 等待两个任务都完成
        task1 = await manager.wait_for_task(tid1, timeout=5)
        task2 = await manager.wait_for_task(tid2, timeout=5)

        # 关键断言：第二个任务不应丢失
        assert task2 is not None, "在处理器运行期间提交的任务丢失了"
        assert task2.status == TaskStatus.COMPLETED
        assert 2 in completed_ids


class TestConcurrencyIntegration:
    """并发集成测试"""

    @pytest.mark.asyncio
    async def test_rate_limiter_with_manager(self):
        """测试速率限制器与并发管理器协同工作"""
        limiter = RateLimiter(max_requests=10, window_seconds=60)
        ConcurrencyManager._instance = None
        manager = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=2,
            max_concurrent_llm_calls=3,
        )

        async def handler(payload):
            await asyncio.sleep(0.05)
            return {"done": True}

        allowed_count = 0
        for i in range(15):
            if await limiter.is_allowed("test_client"):
                await manager.submit_task(f"task_{i}", {}, handler)
                allowed_count += 1

        assert allowed_count == 10

        ConcurrencyManager._instance = None

    @pytest.mark.asyncio
    async def test_true_concurrent_execution(self):
        """证明多个任务真正并发执行，而非串行"""
        ConcurrencyManager._instance = None
        manager = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=3,
            max_concurrent_llm_calls=5,
            max_queue_size=50,
        )

        timestamps: Dict[str, list] = {"starts": [], "finishes": []}
        lock = asyncio.Lock()

        async def slow_handler(payload):
            async with lock:
                timestamps["starts"].append(time.time())
            await asyncio.sleep(0.5)
            async with lock:
                timestamps["finishes"].append(time.time())
            return {"id": payload["id"]}

        task_ids = []
        for i in range(6):
            tid = await manager.submit_task("batch", {"id": i}, slow_handler)
            task_ids.append(tid)

        start_time = time.time()
        for tid in task_ids:
            task = await manager.wait_for_task(tid, timeout=15)
            assert task.status == TaskStatus.COMPLETED
        total_time = time.time() - start_time

        # 如果串行: 6 × 0.5s = 3s
        # 并发度3: 两批 × 0.5s ≈ 1s，加上开销应 < 2s
        assert total_time < 2.0, (
            f"Tasks ran serially ({total_time:.2f}s), expected concurrent (< 2s)"
        )

        ConcurrencyManager._instance = None

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """测试信号量确实限制了并发数"""
        ConcurrencyManager._instance = None
        manager = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=2,
            max_concurrent_llm_calls=2,
        )

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def handler(payload):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.2)
            async with lock:
                current_concurrent -= 1
            return {}

        task_ids = []
        for i in range(6):
            tid = await manager.submit_task("test", {}, handler)
            task_ids.append(tid)

        for tid in task_ids:
            await manager.wait_for_task(tid, timeout=15)

        # 工作流信号量限制为2，但任务是在队列中顺序处理的
        # 所以实际并发取决于队列处理方式
        assert max_concurrent <= 2

        ConcurrencyManager._instance = None
