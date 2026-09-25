"""进程资源采样（psutil，已是项目依赖）。

Phase 1 轻量版：单次采样 CPU/内存/存活时长；
供 SandboxController.status() 与未来的 QA Agent 消费。
"""

import time
from typing import Dict, Optional


def sample_process(pid: int) -> Optional[Dict[str, object]]:
    """采样一个进程的资源占用；进程不存在返回 None。"""
    try:
        import psutil

        p = psutil.Process(pid)
        with p.oneshot():
            return {
                "pid": pid,
                "name": p.name(),
                "status": p.status(),
                "cpu_percent": p.cpu_percent(interval=0.05),
                "memory_mb": round(p.memory_info().rss / 1024 / 1024, 1),
                "create_time": p.create_time(),
                "age_seconds": round(time.time() - p.create_time(), 1),
                "children": len(p.children(recursive=True)),
            }
    except Exception:  # noqa: BLE001 — 进程消失/权限不足均视为采样失败
        return None


def format_metrics(m: Optional[Dict[str, object]]) -> str:
    if not m:
        return "无活动进程"
    return (
        f"CPU {m['cpu_percent']}% | 内存 {m['memory_mb']}MB | "
        f"子进程 {m['children']} | 存活 {m['age_seconds']}s"
    )
