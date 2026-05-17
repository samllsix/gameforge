"""GameForge 万级并发压力测试"""
import asyncio, time, json, sys, os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import logging
logging.getLogger().setLevel(logging.CRITICAL)

from src.core.concurrency import RateLimiter, ConcurrencyManager, TaskStatus

results = []
lines = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"{ts} {msg}"
    lines.append(line)
    print(line)

def show(name, total, ok, rej, elapsed):
    ops = total / elapsed if elapsed > 0 else 0
    log(f"  [{name}] total={total:,} ok={ok:,} rej={rej:,} time={elapsed:.2f}s throughput={ops:,.0f}/s")
    results.append({"name": name, "total": total, "ok": ok, "rej": rej, "time": f"{elapsed:.2f}", "ops": f"{ops:,.0f}"})


async def main():
    log("GameForge 并发压力测试 (万级)")
    log(f"时间: {datetime.now()}")
    log(f"MIMO_API_KEY: {'已配置' if os.getenv('MIMO_API_KEY') else '未配置'}")

    # Phase 1
    log("\n" + "=" * 60)
    log("Phase 1: 速率限制器")
    log("=" * 60)

    log("[1.1] 单客户端 10,000 次 (限100/窗口)")
    rl = RateLimiter(max_requests=100, window_seconds=60)
    t0 = time.perf_counter()
    res = await asyncio.gather(*[rl.is_allowed("c1") for _ in range(10000)])
    ok = sum(1 for r in res if r)
    show("rl_single", 10000, ok, 10000 - ok, time.perf_counter() - t0)

    log("[1.2] 5,000 客户端并发 (限10/窗口)")
    rl2 = RateLimiter(max_requests=10, window_seconds=60)
    t0 = time.perf_counter()
    res = await asyncio.gather(*[rl2.is_allowed(f"c{i}") for i in range(5000)])
    ok = sum(1 for r in res if r)
    show("rl_5k_clients", 5000, ok, 5000 - ok, time.perf_counter() - t0)

    log("[1.3] 突发 10,000 (100客户端, 限50)")
    rl3 = RateLimiter(max_requests=50, window_seconds=2)
    t0 = time.perf_counter()
    res = await asyncio.gather(*[rl3.is_allowed(f"c{i % 100}") for i in range(10000)])
    ok = sum(1 for r in res if r)
    show("rl_burst", 10000, ok, 10000 - ok, time.perf_counter() - t0)

    # Phase 2
    log("\n" + "=" * 60)
    log("Phase 2: 任务队列")
    log("=" * 60)

    log("[2.1] 提交 2,000 任务 (5并发)")
    ConcurrencyManager._instance = None
    mgr = await ConcurrencyManager.get_instance(
        max_concurrent_workflows=5, max_concurrent_llm_calls=10, max_queue_size=5000
    )

    async def h(p):
        return {"id": p["id"]}

    t0 = time.perf_counter()
    tids = []
    for i in range(2000):
        tid = await mgr.submit_task("s", {"id": i}, h, priority=i % 5)
        tids.append(tid)

    ok = 0
    for tid in tids[:500]:
        t = await mgr.wait_for_task(tid, timeout=15)
        if t and t.status == TaskStatus.COMPLETED:
            ok += 1
    show("tq_2k", 2000, ok, 0, time.perf_counter() - t0)
    log(f"  stats: {json.dumps(mgr.get_stats())}")
    ConcurrencyManager._instance = None

    # Phase 3
    log("\n" + "=" * 60)
    log("Phase 3: HTTP并发模拟")
    log("=" * 60)

    log("[3.1] 中间件链 10,000 请求 (500客户端)")
    rl4 = RateLimiter(max_requests=100, window_seconds=60)
    sem = asyncio.Semaphore(20)

    async def req(idx):
        if not await rl4.is_allowed(f"c{idx % 500}"):
            return "r"
        async with sem:
            await asyncio.sleep(0.0001)
        return "ok"

    t0 = time.perf_counter()
    res = await asyncio.gather(*[req(i) for i in range(10000)])
    ok = sum(1 for r in res if r == "ok")
    show("http_10k", 10000, ok, 10000 - ok, time.perf_counter() - t0)

    log("[3.2] DDoS模拟 5,000 (10攻击者)")
    rl5 = RateLimiter(max_requests=50, window_seconds=1)

    async def atk(i):
        return "ok" if await rl5.is_allowed(f"a{i % 10}") else "b"

    t0 = time.perf_counter()
    res = await asyncio.gather(*[atk(i) for i in range(5000)])
    ok = sum(1 for r in res if r == "ok")
    show("ddos_5k", 5000, ok, 5000 - ok, time.perf_counter() - t0)

    # Phase 4
    log("\n" + "=" * 60)
    log("Phase 4: LLM连接池")
    log("=" * 60)

    from src.utils.llm_client import LLMClientPool, get_llm_client

    log("[4.1] 连接池 10,000 次获取")
    pool = await LLMClientPool.get_instance()
    t0 = time.perf_counter()
    clients = [pool.get_client("https://t.com", "k") for _ in range(10000)]
    show("pool_10k", 10000, 10000, 0, time.perf_counter() - t0)
    log(f"  unique instances: {len(set(id(c) for c in clients))} (should be 1)")

    log("[4.2] get_llm_client 5,000 次")
    cfg = {"llm": {"default_model": "mimo-v2.5-pro", "base_url": "https://t.com"}}
    t0 = time.perf_counter()
    for _ in range(5000):
        get_llm_client(cfg)
    show("cache_5k", 5000, 5000, 0, time.perf_counter() - t0)

    # Phase 5
    log("\n" + "=" * 60)
    log("Phase 5: Mimo API 真实调用")
    log("=" * 60)

    api_key = os.getenv("MIMO_API_KEY", "")
    if api_key:
        from src.utils.llm_client import LLMClient

        client = LLMClient(
            {
                "llm": {
                    "default_model": "mimo-v2.5-pro",
                    "base_url": os.getenv(
                        "MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
                    ),
                }
            }
        )

        log("[5.1] 顺序调用 3 次")
        for i in range(3):
            try:
                t0 = time.perf_counter()
                r = await client.chat(
                    [{"role": "user", "content": f'回复"第{i+1}次OK"'}], max_tokens=10
                )
                lat = (time.perf_counter() - t0) * 1000
                log(f"  [{i+1}] {r[:30]} ({lat:.0f}ms)")
            except Exception as e:
                log(f"  [{i+1}] ERROR: {e}")

        log("[5.2] 并发调用 5 次")

        async def c(i):
            try:
                t0 = time.perf_counter()
                r = await client.chat(
                    [{"role": "user", "content": f'回复"C{i}ok"'}], max_tokens=10
                )
                return {"ok": True, "lat": (time.perf_counter() - t0) * 1000, "r": r[:20]}
            except Exception as e:
                return {"ok": False, "err": str(e)}

        t0 = time.perf_counter()
        res = await asyncio.gather(*[c(i) for i in range(5)])
        for i, r in enumerate(res):
            if r["ok"]:
                log(f"  [{i+1}] {r['r']} ({r['lat']:.0f}ms)")
            else:
                log(f"  [{i+1}] ERROR: {r['err']}")
    else:
        log("  [SKIP] MIMO_API_KEY 未配置")

    # Phase 6
    log("\n" + "=" * 60)
    log("Phase 6: 混合负载 10,000")
    log("=" * 60)

    ConcurrencyManager._instance = None
    mgr2 = await ConcurrencyManager.get_instance(
        max_concurrent_workflows=10, max_concurrent_llm_calls=20, max_queue_size=20000
    )
    rl6 = RateLimiter(max_requests=500, window_seconds=60)

    async def op(i):
        if not await rl6.is_allowed(f"u{i % 1000}"):
            return "r"

        async def h2(p):
            return {}

        await mgr2.submit_task("m", {}, h2)
        return "ok"

    t0 = time.perf_counter()
    res = await asyncio.gather(*[op(i) for i in range(10000)])
    ok = sum(1 for r in res if r == "ok")
    show("mixed_10k", 10000, ok, 10000 - ok, time.perf_counter() - t0)
    ConcurrencyManager._instance = None

    # Summary
    log("\n" + "=" * 60)
    log("汇总报告")
    log("=" * 60)
    total = sum(r["total"] for r in results)
    ok = sum(r["ok"] for r in results)
    rej = sum(r["rej"] for r in results)
    t = sum(float(r["time"]) for r in results)
    log(f"  用例: {len(results)} | 总操作: {total:,} | 成功: {ok:,} | 限流: {rej:,} | 耗时: {t:.2f}s")
    for r in results:
        log(f"  {r['name']:<20} {r['total']:>8,} ok={r['ok']:>8,} rej={r['rej']:>6,} {r['time']}s {r['ops']}/s")

    p = Path("logs") / f"stress_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    p.parent.mkdir(exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    log(f"\n日志已保存: {p}")


if __name__ == "__main__":
    asyncio.run(main())
