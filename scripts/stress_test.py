"""GameForge - 并发压力测试脚本（万级）

使用mimo模型进行真实API验证。
"""

import asyncio
import time
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()


@dataclass
class TestResult:
    name: str
    total: int = 0
    ok: int = 0
    fail: int = 0
    rejected: int = 0
    elapsed: float = 0.0
    ops_per_sec: float = 0.0
    latencies: List[float] = field(default_factory=list)
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0

    def calc(self):
        if self.latencies:
            s = sorted(self.latencies)
            self.p50 = s[int(len(s)*0.5)]
            self.p95 = s[int(len(s)*0.95)]
            self.p99 = s[int(len(s)*0.99)]
        if self.elapsed > 0:
            self.ops_per_sec = self.total / self.elapsed


class StressTest:
    def __init__(self):
        self.results: List[TestResult] = []
        self.lines: List[str] = []

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"{ts} {msg}"
        self.lines.append(line)
        print(line)

    def show(self, r: TestResult):
        self.log(f"  操作数={r.total:,} 成功={r.ok:,} 失败={r.fail:,} 拒绝={r.rejected:,}")
        self.log(f"  耗时={r.elapsed:.2f}s 吞吐={r.ops_per_sec:,.0f} ops/s")
        if r.latencies:
            self.log(f"  P50={r.p50:.1f}ms P95={r.p95:.1f}ms P99={r.p99:.1f}ms")

    # ===== Phase 1: 速率限制器 =====
    async def phase1_rate_limiter(self):
        self.log("\n" + "="*60)
        self.log("Phase 1: 速率限制器压测")
        self.log("="*60)
        from src.core.concurrency import RateLimiter

        # 1.1 单客户端10000次
        self.log("\n[1.1] 单客户端 10,000 次检查 (限100/窗口)")
        limiter = RateLimiter(max_requests=100, window_seconds=60)
        r = TestResult("rl_single_10k")
        start = time.perf_counter()
        tasks = [limiter.is_allowed("c1") for _ in range(10000)]
        for res in await asyncio.gather(*tasks):
            r.total += 1
            r.ok += 1 if res else 0
            r.rejected += 0 if res else 1
        r.elapsed = time.perf_counter() - start
        r.calc()
        self.results.append(r)
        self.show(r)

        # 1.2 5000客户端并发
        self.log("\n[1.2] 5,000 客户端并发检查 (限10/窗口)")
        limiter2 = RateLimiter(max_requests=10, window_seconds=60)
        r2 = TestResult("rl_5k_clients")
        start = time.perf_counter()
        tasks = [limiter2.is_allowed(f"c{i}") for i in range(5000)]
        for res in await asyncio.gather(*tasks):
            r2.total += 1
            r2.ok += 1 if res else 0
            r2.rejected += 0 if res else 1
        r2.elapsed = time.perf_counter() - start
        r2.calc()
        self.results.append(r2)
        self.show(r2)

        # 1.3 突发流量
        self.log("\n[1.3] 突发流量 10,000 请求 (100客户端, 限50/窗口)")
        limiter3 = RateLimiter(max_requests=50, window_seconds=2)
        r3 = TestResult("rl_burst_10k")
        start = time.perf_counter()
        tasks = [limiter3.is_allowed(f"c{i%100}") for i in range(10000)]
        for res in await asyncio.gather(*tasks):
            r3.total += 1
            r3.ok += 1 if res else 0
            r3.rejected += 0 if res else 1
        r3.elapsed = time.perf_counter() - start
        r3.calc()
        self.results.append(r3)
        self.show(r3)

    # ===== Phase 2: 任务队列 =====
    async def phase2_task_queue(self):
        self.log("\n" + "="*60)
        self.log("Phase 2: 任务队列压测")
        self.log("="*60)
        from src.core.concurrency import ConcurrencyManager, TaskStatus

        ConcurrencyManager._instance = None
        mgr = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=10,
            max_concurrent_llm_calls=20,
            max_queue_size=20000,
        )

        # 2.1 提交5000任务
        self.log("\n[2.1] 提交 5,000 任务 (10并发)")
        r = TestResult("tq_5k_submit")
        async def handler(p):
            return {"id": p["id"]}

        start = time.perf_counter()
        tids = []
        for i in range(5000):
            tid = await mgr.submit_task("stress", {"id": i}, handler, priority=i%5)
            tids.append(tid)
            r.total += 1

        # 等待采样
        sample = tids[:500]
        for tid in sample:
            t = await mgr.wait_for_task(tid, timeout=30)
            if t and t.status == TaskStatus.COMPLETED:
                r.ok += 1
            else:
                r.fail += 1

        r.elapsed = time.perf_counter() - start
        r.calc()
        self.results.append(r)
        self.show(r)
        stats = mgr.get_stats()
        self.log(f"  统计: submitted={stats['total_submitted']:,} completed={stats['total_completed']:,}")

        # 2.2 优先级测试
        self.log("\n[2.2] 优先级队列 2,000 任务")
        ConcurrencyManager._instance = None
        mgr2 = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=3,
            max_concurrent_llm_calls=5,
            max_queue_size=5000,
        )
        order = []
        lock = asyncio.Lock()
        async def prio_handler(p):
            async with lock:
                order.append(p["p"])
            return {}

        r2 = TestResult("tq_priority_2k")
        start = time.perf_counter()
        tids2 = []
        for i in range(2000):
            tid = await mgr2.submit_task("prio", {"p": i%5}, prio_handler, priority=i%5)
            tids2.append(tid)
            r2.total += 1

        for tid in tids2[:200]:
            t = await mgr2.wait_for_task(tid, timeout=15)
            if t and t.status == TaskStatus.COMPLETED:
                r2.ok += 1
            else:
                r2.fail += 1
        r2.elapsed = time.perf_counter() - start
        r2.calc()
        self.results.append(r2)
        self.show(r2)
        if len(order) >= 50:
            self.log(f"  前50执行优先级: {order[:50]}")

        ConcurrencyManager._instance = None

    # ===== Phase 3: HTTP并发模拟 =====
    async def phase3_http_sim(self):
        self.log("\n" + "="*60)
        self.log("Phase 3: HTTP并发请求模拟")
        self.log("="*60)
        from src.core.concurrency import RateLimiter

        # 3.1 中间件链
        self.log("\n[3.1] 中间件链 10,000 请求 (500客户端)")
        rl = RateLimiter(max_requests=100, window_seconds=60)
        sem = asyncio.Semaphore(20)
        r = TestResult("http_10k")

        async def req(idx):
            cid = f"c{idx%500}"
            if not await rl.is_allowed(cid):
                return "limited"
            async with sem:
                await asyncio.sleep(0.0001)
            return "ok"

        start = time.perf_counter()
        tasks = [req(i) for i in range(10000)]
        for res in await asyncio.gather(*tasks):
            r.total += 1
            if res == "ok": r.ok += 1
            else: r.rejected += 1
        r.elapsed = time.perf_counter() - start
        r.calc()
        self.results.append(r)
        self.show(r)

        # 3.2 DDoS模拟
        self.log("\n[3.2] DDoS模拟 5,000 请求 (10攻击者IP)")
        rl2 = RateLimiter(max_requests=50, window_seconds=1)
        r2 = TestResult("ddos_5k")

        async def attack(idx):
            c = f"atk{idx%10}"
            if not await rl2.is_allowed(c):
                return "blocked"
            return "ok"

        start = time.perf_counter()
        tasks = [attack(i) for i in range(5000)]
        for res in await asyncio.gather(*tasks):
            r2.total += 1
            if res == "ok": r2.ok += 1
            else: r2.rejected += 1
        r2.elapsed = time.perf_counter() - start
        r2.calc()
        self.results.append(r2)
        self.show(r2)

    # ===== Phase 4: LLM连接池 =====
    async def phase4_llm_pool(self):
        self.log("\n" + "="*60)
        self.log("Phase 4: LLM连接池压测")
        self.log("="*60)
        from src.utils.llm_client import LLMClient, LLMClientPool, get_llm_client

        # 4.1 连接池获取
        self.log("\n[4.1] 连接池 10,000 次并发获取")
        pool = await LLMClientPool.get_instance()
        r = TestResult("pool_10k")
        start = time.perf_counter()
        tasks = [pool.get_client("https://test.com", "key") for _ in range(10000)]
        clients = await asyncio.gather(*tasks)
        r.total = 10000
        r.ok = 10000
        r.elapsed = time.perf_counter() - start
        r.calc()
        self.results.append(r)
        self.show(r)
        unique = len(set(id(c) for c in clients))
        self.log(f"  唯一实例数: {unique} (应为1)")

        # 4.2 get_llm_client缓存
        self.log("\n[4.2] get_llm_client 5,000 次调用")
        config = {"llm": {"default_model": "mimo-v2.5-pro", "base_url": "https://test.com"}}
        r2 = TestResult("client_cache_5k")
        start = time.perf_counter()
        for _ in range(5000):
            get_llm_client(config)
            r2.total += 1
            r2.ok += 1
        r2.elapsed = time.perf_counter() - start
        r2.calc()
        self.results.append(r2)
        self.show(r2)

    # ===== Phase 5: Mimo API 真实调用 =====
    async def phase5_mimo_api(self):
        self.log("\n" + "="*60)
        self.log("Phase 5: Mimo API 真实并发调用")
        self.log("="*60)

        api_key = os.getenv("MIMO_API_KEY", "")
        if not api_key:
            self.log("  [SKIP] MIMO_API_KEY 未配置")
            return

        from src.utils.llm_client import LLMClient
        config = {"llm": {
            "default_model": "mimo-v2.5-pro",
            "base_url": os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
        }}
        client = LLMClient(config)

        # 5.1 顺序调用5次
        self.log("\n[5.1] Mimo 顺序调用 5 次")
        r = TestResult("mimo_seq_5")
        for i in range(5):
            try:
                t0 = time.perf_counter()
                resp = await client.chat([{"role":"user","content":f"回复'第{i+1}次成功'"}], max_tokens=20)
                lat = (time.perf_counter()-t0)*1000
                r.latencies.append(lat)
                r.total += 1; r.ok += 1
                self.log(f"  [{i+1}] {resp[:40]} ({lat:.0f}ms)")
            except Exception as e:
                r.total += 1; r.fail += 1
                self.log(f"  [{i+1}] ERROR: {e}")
        r.elapsed = sum(r.latencies)/1000 if r.latencies else 0
        r.calc()
        self.results.append(r)
        self.show(r)

        # 5.2 并发调用10次
        self.log("\n[5.2] Mimo 并发调用 10 次")
        r2 = TestResult("mimo_conc_10")
        async def call(i):
            try:
                t0 = time.perf_counter()
                resp = await client.chat([{"role":"user","content":f"回复'{i}号OK'"}], max_tokens=10)
                return {"ok":True, "lat":(time.perf_counter()-t0)*1000, "resp":resp[:30]}
            except Exception as e:
                return {"ok":False, "err":str(e)}

        start = time.perf_counter()
        results = await asyncio.gather(*[call(i) for i in range(10)])
        for i, res in enumerate(results):
            r2.total += 1
            if res["ok"]:
                r2.ok += 1; r2.latencies.append(res["lat"])
                self.log(f"  [{i+1}] {res['resp']} ({res['lat']:.0f}ms)")
            else:
                r2.fail += 1
                self.log(f"  [{i+1}] ERROR: {res['err']}")
        r2.elapsed = time.perf_counter() - start
        r2.calc()
        self.results.append(r2)
        self.show(r2)

    # ===== Phase 6: 混合负载 =====
    async def phase6_mixed(self):
        self.log("\n" + "="*60)
        self.log("Phase 6: 混合负载 10,000 操作")
        self.log("="*60)
        from src.core.concurrency import RateLimiter, ConcurrencyManager

        ConcurrencyManager._instance = None
        mgr = await ConcurrencyManager.get_instance(
            max_concurrent_workflows=10, max_concurrent_llm_calls=20, max_queue_size=20000,
        )
        rl = RateLimiter(max_requests=500, window_seconds=60)

        r = TestResult("mixed_10k")
        async def op(idx):
            if not await rl.is_allowed(f"u{idx%1000}"):
                return "limited"
            async def h(p): return {"i":p["i"]}
            await mgr.submit_task("m",{"i":idx},h, priority=idx%5)
            return "ok"

        start = time.perf_counter()
        tasks = [op(i) for i in range(10000)]
        for res in await asyncio.gather(*tasks):
            r.total += 1
            if res == "ok": r.ok += 1
            else: r.rejected += 1
        r.elapsed = time.perf_counter() - start
        r.calc()
        self.results.append(r)
        self.show(r)
        stats = mgr.get_stats()
        self.log(f"  队列: submitted={stats['total_submitted']:,}")
        ConcurrencyManager._instance = None

    # ===== 汇总 =====
    def summary(self):
        self.log("\n" + "="*60)
        self.log("并发压力测试 - 汇总报告")
        self.log("="*60)
        total = sum(r.total for r in self.results)
        ok = sum(r.ok for r in self.results)
        fail = sum(r.fail for r in self.results)
        rej = sum(r.rejected for r in self.results)
        t = sum(r.elapsed for r in self.results)
        self.log(f"  用例: {len(self.results)} | 总操作: {total:,}")
        self.log(f"  成功: {ok:,} ({ok/max(total,1)*100:.1f}%)")
        self.log(f"  失败: {fail:,} | 限流: {rej:,}")
        self.log(f"  总耗时: {t:.2f}s")
        self.log("")
        self.log(f"  {'测试':<25} {'操作':>8} {'成功':>8} {'拒绝':>8} {'耗时':>8} {'吞吐':>10}")
        self.log(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
        for r in self.results:
            self.log(f"  {r.name:<25} {r.total:>8,} {r.ok:>8,} {r.rejected:>8,} {r.elapsed:>7.2f}s {r.ops_per_sec:>8,.0f}/s")

    def save(self) -> str:
        p = Path("logs") / f"stress_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        p.parent.mkdir(exist_ok=True)
        p.write_text("\n".join(self.lines), encoding="utf-8")
        return str(p)


async def main():
    t = StressTest()
    t.log(f"GameForge 并发压力测试 (万级)")
    t.log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    t.log(f"MIMO_API_KEY: {'已配置' if os.getenv('MIMO_API_KEY') else '未配置'}")

    await t.phase1_rate_limiter()
    await t.phase2_task_queue()
    await t.phase3_http_sim()
    await t.phase4_llm_pool()
    await t.phase5_mimo_api()
    await t.phase6_mixed()

    t.summary()
    f = t.save()
    t.log(f"\n日志已保存: {f}")


if __name__ == "__main__":
    asyncio.run(main())
