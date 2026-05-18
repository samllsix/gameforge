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

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
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
        allowed = {"unity", "unreal", "godot", "generic"}
        if engine.lower() not in allowed:
            return {"valid": False, "error": f"不支持的引擎: {engine}，支持: {', '.join(allowed)}"}
        return {"valid": True, "error": None}


# ============================================================
# 安全头中间件
# ============================================================


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全HTTP头中间件"""

    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; img-src 'self' data:;",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for key, value in self.HEADERS.items():
            response.headers[key] = value
        # 移除服务器指纹
        response.headers.pop("server", None)
        return response


# ============================================================
# 请求体大小限制中间件
# ============================================================


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """请求体大小限制"""

    def __init__(self, app, max_size_bytes: int = 1_048_576):  # 1MB默认
        super().__init__(app)
        self.max_size = max_size_bytes

    async def dispatch(self, request: Request, call_next):
        # 检查Content-Length
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "Request body too large",
                    "message": f"请求体超过最大限制 ({self.max_size // 1024}KB)",
                    "max_size_bytes": self.max_size,
                },
            )
        return await call_next(request)


# ============================================================
# 审计日志
# ============================================================


class AuditLogger:
    """安全审计日志"""

    def __init__(self, log_dir: str = "logs/security"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

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
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """API密钥认证中间件"""

    # 不需要认证的路径
    PUBLIC_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app, api_keys: Optional[Dict[str, str]] = None, enabled: bool = False):
        super().__init__(app)
        self.api_keys = api_keys or {}
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        # 未启用时跳过
        if not self.enabled:
            return await call_next(request)

        # 公开路径不需要认证
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        # 从Header或Query获取API Key
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

        if not api_key:
            audit = get_audit_logger()
            await audit.log_event(
                event_type="missing_api_key",
                client_ip=request.client.host if request.client else "unknown",
                path=request.url.path,
                method=request.method,
                severity="WARNING",
            )
            return JSONResponse(
                status_code=401,
                content={"error": "Missing API key", "message": "请提供有效的API密钥"},
            )

        # 验证API Key
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        if api_key not in self.api_keys:
            audit = get_audit_logger()
            await audit.log_auth_attempt(
                client_ip=request.client.host if request.client else "unknown",
                success=False,
                api_key_hash=key_hash,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "Invalid API key", "message": "API密钥无效"},
            )

        # 认证通过
        request.state.client_name = self.api_keys[api_key]
        return await call_next(request)


# ============================================================
# 安全输入验证中间件
# ============================================================


class InputValidationMiddleware(BaseHTTPMiddleware):
    """输入验证中间件 — 自动检测并拦截恶意请求"""

    SKIP_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/stats", "/app"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        audit = get_audit_logger()

        # 检查路径遍历
        if InputValidator.check_path_traversal(request.url.path):
            await audit.log_injection_attempt(
                client_ip, request.url.path, "path_traversal", request.url.path
            )
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid path", "message": "请求路径包含非法字符"},
            )

        # 检查查询参数
        for key, value in request.query_params.items():
            if InputValidator.check_sql_injection(value):
                await audit.log_injection_attempt(
                    client_ip, request.url.path, "sql_injection", f"{key}={value}"
                )
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid parameter", "message": "查询参数包含非法字符"},
                )

        return await call_next(request)


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
