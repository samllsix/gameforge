"""GameForge - API中间件模块

提供认证、限流、日志、并发控制等中间件功能。
"""

import time
import json
import asyncio
from typing import Dict, Any, Optional
from collections import defaultdict
from datetime import datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.core.concurrency import RateLimiter, ConcurrencyManager


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件 — 基于客户端IP限制请求频率"""

    def __init__(
        self,
        app,
        max_requests: int = 100,
        window_seconds: int = 60,
        exclude_paths: Optional[list] = None,
    ):
        super().__init__(app)
        self.limiter = RateLimiter(
            max_requests=max_requests, window_seconds=window_seconds
        )
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]

    async def dispatch(self, request: Request, call_next):
        # 排除不需要限流的路径
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        allowed = await self.limiter.is_allowed(client_ip)

        if not allowed:
            remaining = await self.limiter.get_remaining(client_ip)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "message": f"请求过于频繁，请在{self.limiter.window_seconds}秒后重试",
                    "retry_after": self.limiter.window_seconds,
                },
                headers={"Retry-After": str(self.limiter.window_seconds)},
            )

        response = await call_next(request)
        remaining = await self.limiter.get_remaining(client_ip)
        response.headers["X-RateLimit-Limit"] = str(self.limiter.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    """并发控制中间件 — 限制同时处理的请求数"""

    def __init__(self, app, max_concurrent: int = 20):
        super().__init__(app)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_count = 0
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        # 健康检查不受并发限制
        if request.url.path in ("/health", "/docs", "/openapi.json"):
            return await call_next(request)

        # 尝试获取信号量（非阻塞）
        acquired = self.semaphore.locked()
        if self.semaphore.locked() and self.semaphore._value == 0:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Service overloaded",
                    "message": "服务器繁忙，请稍后重试",
                    "active_requests": self.active_count,
                },
                headers={"Retry-After": "5"},
            )

        async with self.semaphore:
            async with self._lock:
                self.active_count += 1
            try:
                response = await call_next(request)
                response.headers["X-Active-Requests"] = str(self.active_count)
                return response
            finally:
                async with self._lock:
                    self.active_count -= 1


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """请求指标中间件 — 记录请求耗时和统计"""

    def __init__(self, app, log_file: Optional[str] = None):
        super().__init__(app)
        self.log_file = log_file
        self.stats = {
            "total_requests": 0,
            "total_duration": 0.0,
            "status_codes": defaultdict(int),
            "paths": defaultdict(int),
        }
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        response = await call_next(request)

        duration = time.time() - start_time
        async with self._lock:
            self.stats["total_requests"] += 1
            self.stats["total_duration"] += duration
            self.stats["status_codes"][response.status_code] += 1
            self.stats["paths"][request.url.path] += 1

        response.headers["X-Response-Time"] = f"{duration:.3f}s"

        # 异步写日志（不阻塞响应）
        if self.log_file:
            asyncio.create_task(self._write_log(request, response, duration))

        return response

    async def _write_log(self, request: Request, response, duration: float):
        """异步写入请求日志"""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "client_ip": request.client.host if request.client else "unknown",
            }
            import aiofiles

            async with aiofiles.open(self.log_file, "a", encoding="utf-8") as f:
                await f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 日志写入失败不影响请求处理

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
