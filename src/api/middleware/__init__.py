"""GameForge - API中间件模块

提供认证、限流、日志、并发控制等中间件功能。
所有中间件使用纯ASGI实现，不缓冲流式响应。
"""

import time
import json
import asyncio
from typing import Dict, Any, Optional
from collections import defaultdict
from datetime import datetime

from src.core.concurrency import RateLimiter, ConcurrencyManager


def _get_client_ip(scope) -> str:
    """从ASGI scope获取客户端IP"""
    client = scope.get("client")
    return client[0] if client else "unknown"


def _get_path(scope) -> str:
    """从ASGI scope获取请求路径"""
    return scope.get("path", "")


async def _send_json_error(send, status_code: int, body: dict, extra_headers: dict = None):
    """发送JSON错误响应"""
    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(content)).encode()),
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            headers.append((k.encode(), str(v).encode()))

    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": headers,
    })
    await send({
        "type": "http.response.body",
        "body": content,
    })


class RateLimitMiddleware:
    """速率限制中间件 — 基于客户端IP限制请求频率（纯ASGI）"""

    def __init__(
        self,
        app,
        max_requests: int = 100,
        window_seconds: int = 60,
        exclude_paths: Optional[list] = None,
    ):
        self.app = app
        self.limiter = RateLimiter(
            max_requests=max_requests, window_seconds=window_seconds
        )
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = _get_path(scope)

        # 排除不需要限流的路径
        if path in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        client_ip = _get_client_ip(scope)
        allowed = await self.limiter.is_allowed(client_ip)

        if not allowed:
            await _send_json_error(send, 429, {
                "error": "Rate limit exceeded",
                "message": f"请求过于频繁，请在{self.limiter.window_seconds}秒后重试",
                "retry_after": self.limiter.window_seconds,
            }, {"Retry-After": str(self.limiter.window_seconds)})
            return

        # 添加限流头到响应
        remaining = await self.limiter.get_remaining(client_ip)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(self.limiter.max_requests).encode()))
                headers.append((b"x-ratelimit-remaining", str(remaining).encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ConcurrencyLimitMiddleware:
    """并发控制中间件 — 限制同时处理的请求数（纯ASGI）"""

    def __init__(self, app, max_concurrent: int = 20):
        self.app = app
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_count = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = _get_path(scope)

        # 健康检查不受并发限制
        if path in ("/health", "/docs", "/openapi.json"):
            await self.app(scope, receive, send)
            return

        # 尝试获取信号量（非阻塞）
        if self.semaphore.locked() and self.semaphore._value == 0:
            await _send_json_error(send, 503, {
                "error": "Service overloaded",
                "message": "服务器繁忙，请稍后重试",
                "active_requests": self.active_count,
            }, {"Retry-After": "5"})
            return

        async with self.semaphore:
            async with self._lock:
                self.active_count += 1
            try:
                async def send_with_headers(message):
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        headers.append((b"x-active-requests", str(self.active_count).encode()))
                        message["headers"] = headers
                    await send(message)

                await self.app(scope, receive, send_with_headers)
            finally:
                async with self._lock:
                    self.active_count -= 1


class RequestMetricsMiddleware:
    """请求指标中间件 — 记录请求耗时和统计（纯ASGI）"""

    def __init__(self, app, log_file: Optional[str] = None):
        self.app = app
        self.log_file = log_file
        self.stats = {
            "total_requests": 0,
            "total_duration": 0.0,
            "status_codes": defaultdict(int),
            "paths": defaultdict(int),
        }
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        path = _get_path(scope)

        # 捕获状态码
        status_code = [200]

        async def send_with_metrics(message):
            if message["type"] == "http.response.start":
                status_code[0] = message.get("status", 200)
                headers = list(message.get("headers", []))
                duration = time.time() - start_time
                headers.append((b"x-response-time", f"{duration:.3f}s".encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_metrics)

        duration = time.time() - start_time
        async with self._lock:
            self.stats["total_requests"] += 1
            self.stats["total_duration"] += duration
            self.stats["status_codes"][status_code[0]] += 1
            self.stats["paths"][path] += 1

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = self.stats["total_requests"]
        return {
            "total_requests": total,
            "avg_duration_ms": round(
                (self.stats["total_duration"] / total * 1000) if total > 0 else 0, 2
            ),
            "status_codes": dict(self.stats["status_codes"]),
            "top_paths": dict(
                sorted(
                    self.stats["paths"].items(), key=lambda x: x[1], reverse=True
                )[:10]
            ),
        }


class RequestLogger:
    """请求日志记录器（兼容旧代码）"""

    def __init__(self, log_file: Optional[str] = None):
        self.log_file = log_file
        self.requests = []

    def log(
        self,
        method: str,
        path: str,
        status_code: int,
        duration: float,
        client_ip: str = "",
    ):
        """记录请求"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration": duration,
            "client_ip": client_ip,
        }
        self.requests.append(entry)

        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def get_stats(self) -> Dict[str, Any]:
        """获取请求统计"""
        if not self.requests:
            return {"total": 0}
        total = len(self.requests)
        avg_duration = sum(r["duration"] for r in self.requests) / total
        status_codes = defaultdict(int)
        for r in self.requests:
            status_codes[r["status_code"]] += 1
        return {
            "total": total,
            "avg_duration": avg_duration,
            "status_codes": dict(status_codes),
        }


class APIKeyAuth:
    """API密钥认证"""

    def __init__(self, api_keys: Optional[Dict[str, str]] = None):
        self.api_keys = api_keys or {}

    def validate(self, api_key: str) -> bool:
        """验证API密钥"""
        return api_key in self.api_keys

    def get_client(self, api_key: str) -> Optional[str]:
        """获取客户端名称"""
        return self.api_keys.get(api_key)
