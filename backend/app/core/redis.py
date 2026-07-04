"""Redis 连接池 - 可选组件，连接失败不阻断应用启动。"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# redis.asyncio 在 redis>=5 中可用
try:
    import redis.asyncio as aioredis
    from redis.asyncio.connection import ConnectionPool
    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore
    ConnectionPool = None  # type: ignore
    _REDIS_AVAILABLE = False


_redis_pool: Optional["ConnectionPool"] = None
_redis_client = None


async def init_redis() -> None:
    """初始化 Redis 连接池。连接失败时仅记录警告，不抛出异常。"""
    global _redis_pool, _redis_client
    if not _REDIS_AVAILABLE:
        logger.warning("redis 包未安装，跳过 Redis 初始化。")
        return
    try:
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
        )
        _redis_client = aioredis.Redis(connection_pool=_redis_pool)
        await _redis_client.ping()
        logger.info("Redis 连接成功: %s", settings.REDIS_URL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 连接失败（不阻断启动）: %s", exc)
        _redis_pool = None
        _redis_client = None


async def close_redis() -> None:
    """关闭 Redis 连接池。"""
    global _redis_pool, _redis_client
    if _redis_pool is not None:
        try:
            await _redis_pool.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭 Redis 连接池时出错: %s", exc)
    _redis_pool = None
    _redis_client = None


def get_redis():
    """返回 Redis 客户端实例，未连接时返回 None。"""
    return _redis_client


def is_redis_available() -> bool:
    """Redis 是否可用。"""
    return _redis_client is not None
