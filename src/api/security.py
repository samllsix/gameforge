"""GameForge - API安全模块

提供输入验证、安全头、审计日志、注入防护等安全功能。
"""

import re
import hashlib
import time
import json
import asyncio
from typing import Dict, Any, Optional, List, Set
from datetime import datetime
from pathlib import Path

from starlette.requests import Request
import structlog

logger = structlog.get_logger()


# ============================================================
# 输入验证与消毒
# ============================================================


class InputValidator:
    """输入验证器 — 防止注入攻击"""

    # Prompt注入检测模式
    PROMPT_INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now\s+",
        r"system\s*:\s*you\s+are",
        r"<\|system\|>",
        r"<\|endoftext\|>",
        r"ignore\s+above",
        r"disregard\s+(all\s+)?prior",
        r"new\s+instructions\s*:",
        r"override\s+instructions",
        r"forget\s+(everything|all|previous)",
        r"act\s+as\s+if\s+you\s+are",
        r"pretend\s+you\s+are",
        r"jailbreak",
        r"DAN\s+mode",
        r"developer\s+mode",
    ]

    # 路径遍历模式
    PATH_TRAVERSAL_PATTERNS = [
        r"\.\./",
        r"\.\.\\",
        r"%2e%2e",
        r"%252e%252e",
    ]

    # SQL注入模式
    SQL_INJECTION_PATTERNS = [
        r"('\s*(OR|AND)\s+')",
        r"(;\s*(DROP|DELETE|INSERT|UPDATE|ALTER)\s+)",
        r"(UNION\s+SELECT)",
        r"(--\s*$)",
        r"(/\*.*\*/)",
    ]

    @classmethod
    def check_prompt_injection(cls, text: str) -> Optional[str]:
        """检测Prompt注入攻击

        Args:
            text: 输入文本

        Returns:
            检测到的攻击模式，未检测到返回None
        """
        text_lower = text.lower()
        for pattern in cls.PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return pattern
        return None

    @classmethod
    def check_path_traversal(cls, path: str) -> bool:
        """检测路径遍历攻击"""
        for pattern in cls.PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return True
        return False

    @classmethod
    def check_sql_injection(cls, text: str) -> bool:
        """检测SQL注入"""
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """消毒文件名 — 移除危险字符"""
        # 只保留字母数字、下划线、连字符、点
        sanitized = re.sub(r"[^\w\-\.]", "_", filename)
        # 防止路径遍历
        sanitized = sanitized.replace("..", "_")
        # 限制长度
        return sanitized[:255]

    @classmethod
    def validate_requirements(cls, text: str) -> Dict[str, Any]:
        """验证需求文本

        Returns:
            {"valid": bool, "error": str|None, "sanitized": str}
        """
        if not text or not text.strip():
            return {"valid": False, "error": "需求文本不能为空", "sanitized": ""}

        if len(text) > 50000:
            return {
                "valid": False,
                "error": "需求文本超过最大长度限制 (50,000字符)",
                "sanitized": "",
            }

        # 检测Prompt注入
        injection = cls.check_prompt_injection(text)
        if injection:
            logger.warning("prompt_injection_detected", pattern=injection, text_preview=text[:100])
            return {
                "valid": False,
                "error": "输入包含可疑内容，请重新组织需求描述",
                "sanitized": "",
            }

        # 检测SQL注入
        if cls.check_sql_injection(text):
            logger.warning("sql_injection_detected", text_preview=text[:100])
            return {
                "valid": False,
                "error": "输入包含非法字符",
                "sanitized": "",
            }

        # 基本消毒：移除控制字符
        sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

        return {"valid": True, "error": None, "sanitized": sanitized}

    @classmethod
    def validate_project_name(cls, name: str) -> Dict[str, Any]:
        """验证项目名称"""
        if not name or not name.strip():
            return {"valid": False, "error": "项目名称不能为空"}

        if len(name) > 200:
            return {"valid": False, "error": "项目名称过长"}

        # 只允许字母数字、中文、空格、连字符、下划线
        if not re.match(r"^[\w\s\-一-鿿]+$", name):
            return {"valid": False, "error": "项目名称包含非法字符"}

        return {"valid": True, "error": None}

    @classmethod
    def validate_engine(cls, engine: str) -> Dict[str, Any]:
        """验证引擎类型"""
        allowed = {"godot"}
        if engine.lower() not in allowed:
            return {"valid": False, "error": f"不支持的引擎: {engine}，支持: {', '.join(allowed)}"}
        return {"valid": True, "error": None}


# ============================================================
# 安全头中间件
# ============================================================


class SecurityHeadersMiddleware:
    """安全HTTP头中间件（纯ASGI实现，不缓冲流式响应）"""

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"x-xss-protection", b"1; mode=block"),
        (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cache-control", b"no-store, no-cache, must-revalidate"),
        (b"pragma", b"no-cache"),
    ]

    # API/JSON 等非渲染响应：严格 CSP（无 inline script）
    CSP_STRICT = b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    # 后端自渲染的 HTML UI（/dashboard /app /demo）：内联脚本 + blob 预览帧 + Google Fonts
    # 这些页面是第一方可信内容（随服务端一起发布），非用户生成，放开 inline 不引入注入面
    CSP_HTML = (
        b"default-src 'self'; "
        b"script-src 'self' 'unsafe-inline'; "
        b"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        b"font-src 'self' data: https://fonts.gstatic.com; "
        b"img-src 'self' data: blob:; "
        b"connect-src 'self' blob:; "
        b"frame-ancestors 'none'"
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # 添加安全头
                for key, value in self.HEADERS:
                    headers.append((key, value))
                # HTML 文档放宽 CSP（UI 内联脚本/预览 blob 帧），其余响应保持严格
                content_type = next(
                    (v for k, v in headers if k.lower() == b"content-type"), b""
                )
                csp = self.CSP_HTML if b"text/html" in content_type else self.CSP_STRICT
                headers.append((b"content-security-policy", csp))
                # 移除服务器指纹
                headers = [(k, v) for k, v in headers if k != b"server"]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ============================================================
# 请求体大小限制中间件
# ============================================================


class RequestBodyLimitMiddleware:
    """请求体大小限制（纯ASGI）"""

    def __init__(self, app, max_size_bytes: int = 1_048_576):  # 1MB默认
        self.app = app
        self.max_size = max_size_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 检查Content-Length
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length", b"").decode()
        if content_length and int(content_length) > self.max_size:
            content = json.dumps({
                "error": "Request body too large",
                "message": f"请求体超过最大限制 ({self.max_size // 1024}KB)",
                "max_size_bytes": self.max_size,
            }, ensure_ascii=False).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(content)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": content})
            return

        await self.app(scope, receive, send)


# ============================================================
# 审计日志
# ============================================================


class AuditLogger:
    """安全审计日志"""

    def __init__(self, log_dir: str = "logs/security"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _write_audit_log(self, log_file: Path, entry: Dict):
        """同步写入审计日志（在线程池中调用）"""
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def log_event(
        self,
        event_type: str,
        client_ip: str,
        path: str,
        method: str,
        details: Optional[Dict] = None,
        severity: str = "INFO",
    ):
        """记录安全事件"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "severity": severity,
            "client_ip": client_ip,
            "method": method,
            "path": path,
            "details": details or {},
        }

        async with self._lock:
            log_file = self.log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
            try:
                # 将同步文件 I/O 放到线程池执行，避免阻塞事件循环
                await asyncio.to_thread(self._write_audit_log, log_file, entry)
            except Exception:
                pass

        # 高严重度事件同时记录到structlog
        if severity in ("WARNING", "ERROR", "CRITICAL"):
            logger.warning("security_event", **entry)

    async def log_auth_attempt(
        self, client_ip: str, success: bool, api_key_hash: str
    ):
        """记录认证尝试"""
        await self.log_event(
            event_type="auth_attempt",
            client_ip=client_ip,
            path="/auth",
            method="AUTH",
            details={
                "success": success,
                "api_key_hash": api_key_hash[:16] + "...",
            },
            severity="INFO" if success else "WARNING",
        )

    async def log_injection_attempt(
        self, client_ip: str, path: str, injection_type: str, preview: str
    ):
        """记录注入攻击尝试"""
        await self.log_event(
            event_type="injection_attempt",
            client_ip=client_ip,
            path=path,
            method="SECURITY",
            details={"injection_type": injection_type, "preview": preview[:200]},
            severity="ERROR",
        )

    async def log_rate_limit_exceeded(self, client_ip: str, path: str):
        """记录速率限制触发"""
        await self.log_event(
            event_type="rate_limit_exceeded",
            client_ip=client_ip,
            path=path,
            method="RATE_LIMIT",
            severity="WARNING",
        )


# 全局审计日志实例
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


# ============================================================
# API密钥认证中间件
# ============================================================


class APIKeyAuthMiddleware:
    """API密钥认证中间件（纯ASGI）"""

    PUBLIC_PATHS = {
        "/",
        "/dashboard",
        "/digital",
        "/demo",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    def __init__(self, app, api_keys: Optional[Dict[str, str]] = None, enabled: bool = False):
        self.app = app
        self.api_keys = api_keys or {}
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.PUBLIC_PATHS or path.startswith("/static/") or path.startswith("/play/"):
            await self.app(scope, receive, send)
            return

        # 从header获取API Key
        headers = dict(scope.get("headers", []))
        api_key = headers.get(b"x-api-key", b"").decode()

        client_ip = scope.get("client", ("unknown",))[0]

        if not api_key:
            audit = get_audit_logger()
            await audit.log_event(
                event_type="missing_api_key",
                client_ip=client_ip,
                path=path,
                method=scope.get("method", ""),
                severity="WARNING",
            )
            content = json.dumps({"error": "Missing API key", "message": "请提供有效的API密钥"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(content)).encode())]})
            await send({"type": "http.response.body", "body": content})
            return

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        if api_key not in self.api_keys:
            audit = get_audit_logger()
            await audit.log_auth_attempt(client_ip=client_ip, success=False, api_key_hash=key_hash)
            content = json.dumps({"error": "Invalid API key", "message": "API密钥无效"}).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(content)).encode())]})
            await send({"type": "http.response.body", "body": content})
            return

        await self.app(scope, receive, send)


# ============================================================
# 安全输入验证中间件
# ============================================================


class InputValidationMiddleware:
    """输入验证中间件 — 自动检测并拦截恶意请求（纯ASGI）"""

    SKIP_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/stats", "/dashboard", "/digital"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path in self.SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        client_ip = scope.get("client", ("unknown",))[0]
        audit = get_audit_logger()

        # 检查路径遍历
        if InputValidator.check_path_traversal(path):
            await audit.log_injection_attempt(client_ip, path, "path_traversal", path)
            content = json.dumps({"error": "Invalid path", "message": "请求路径包含非法字符"}).encode()
            await send({"type": "http.response.start", "status": 400,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(content)).encode())]})
            await send({"type": "http.response.body", "body": content})
            return

        # 检查查询参数
        query_string = scope.get("query_string", b"").decode()
        for param in query_string.split("&"):
            if "=" in param:
                key, value = param.split("=", 1)
                if InputValidator.check_sql_injection(value):
                    await audit.log_injection_attempt(client_ip, path, "sql_injection", f"{key}={value}")
                    content = json.dumps({"error": "Invalid parameter", "message": "查询参数包含非法字符"}).encode()
                    await send({"type": "http.response.start", "status": 400,
                                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(content)).encode())]})
                    await send({"type": "http.response.body", "body": content})
                    return

        await self.app(scope, receive, send)


# ============================================================
# CORS配置
# ============================================================


def get_secure_cors_config(
    allowed_origins: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """获取安全的CORS配置

    Args:
        allowed_origins: 允许的源列表，None则使用开发默认值

    Returns:
        CORS配置字典
    """
    if allowed_origins is None:
        # 开发环境默认值
        allowed_origins = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
            "http://localhost:8001",
        ]

    return {
        "allow_origins": allowed_origins,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": [
            "Content-Type",
            "Authorization",
            "X-API-Key",
            "X-Request-ID",
        ],
        "expose_headers": [
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-Response-Time",
            "X-Active-Requests",
        ],
        "max_age": 600,  # 预检缓存10分钟
    }
