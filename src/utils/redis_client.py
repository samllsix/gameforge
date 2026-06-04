"""GameForge - Redis客户端模块

提供统一的Redis连接管理，支持LLM响应缓存等功能。
Redis不可用时自动降级为无缓存模式，不影响主流程。
"""

import os
import json
import hashlib
import logging
from typing import Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("GameForge.redis")

# 全局连接实例（延迟初始化）
_redis_client = None
_redis_available = None  # None=未检测, True/False=已检测


async def get_redis():
    """获取Redis客户端实例（延迟初始化，不可用时返回None）

    Returns:
        redis.asyncio.Redis 实例，或 None（Redis不可用时）
    """
    global _redis_client, _redis_available

    if _redis_available is False:
        return None

    if _redis_client is None:
        try:
            import redis.asyncio as redis
            import yaml

            # 优先从 config.yaml 读取，回退到环境变量
            host = os.getenv("REDIS_HOST", "")
            port = os.getenv("REDIS_PORT", "")
            db = os.getenv("REDIS_DB", "")
            password = os.getenv("REDIS_PASSWORD", "")

            if not host:
                try:
                    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml")
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                    redis_cfg = config.get("redis", {})
                    host = redis_cfg.get("host", "localhost")
                    port = str(redis_cfg.get("port", 6379))
                    db = str(redis_cfg.get("db", 0))
                except Exception:
                    host, port, db = "localhost", "6379", "0"

            host = host or "localhost"
            port = int(port or 6379)
            db = int(db or 0)

            # 空字符串或占位符视为无密码
            if not password or password.startswith("your_"):
                password = None

            _redis_client = redis.Redis(
                host=host,
                port=port,
                password=password,
                db=db,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=False,
            )

            # 测试连接
            await _redis_client.ping()
            _redis_available = True
            logger.info(f"Redis连接成功: {host}:{port}/{db}")

        except ImportError:
            logger.warning("redis包未安装，缓存功能禁用。运行: pip install redis")
            _redis_available = False
            return None
        except Exception as e:
            logger.warning(f"Redis连接失败，缓存功能禁用: {e}")
            _redis_available = False
            return None

    return _redis_client


def make_cache_key(prefix: str, **kwargs) -> str:
    """生成缓存键

    Args:
        prefix: 缓存前缀（如 'llm', 'plan'）
        **kwargs: 用于生成hash的参数

    Returns:
        缓存键字符串
    """
    raw = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
    hash_val = hashlib.md5(raw.encode()).hexdigest()[:16]
    return f"gameforge:{prefix}:{hash_val}"


async def cache_get(key: str) -> Optional[str]:
    """从缓存获取值

    Args:
        key: 缓存键

    Returns:
        缓存的值，不存在或Redis不可用时返回None
    """
    redis = await get_redis()
    if redis is None:
        return None
    try:
        return await redis.get(key)
    except Exception as e:
        logger.warning(f"缓存读取失败: {e}")
        return None


async def cache_set(key: str, value: str, ttl: int = 3600) -> bool:
    """写入缓存

    Args:
        key: 缓存键
        value: 缓存值
        ttl: 过期时间（秒），默认1小时

    Returns:
        是否写入成功
    """
    redis = await get_redis()
    if redis is None:
        return False
    try:
        await redis.setex(key, ttl, value)
        return True
    except Exception as e:
        logger.warning(f"缓存写入失败: {e}")
        return False


async def cache_delete(key: str) -> bool:
    """删除缓存

    Args:
        key: 缓存键

    Returns:
        是否删除成功
    """
    redis = await get_redis()
    if redis is None:
        return False
    try:
        await redis.delete(key)
        return True
    except Exception as e:
        logger.warning(f"缓存删除失败: {e}")
        return False


async def cache_clear(prefix: str = "gameforge") -> int:
    """清理指定前缀的所有缓存

    Args:
        prefix: 缓存前缀

    Returns:
        删除的键数量
    """
    redis = await get_redis()
    if redis is None:
        return 0
    try:
        keys = []
        async for key in redis.scan_iter(match=f"{prefix}:*", count=100):
            keys.append(key)
        if keys:
            return await redis.delete(*keys)
        return 0
    except Exception as e:
        logger.warning(f"缓存清理失败: {e}")
        return 0
