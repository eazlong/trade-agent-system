"""WebSocket 断线待发消息缓存。

消息以 Redis LIST (FIFO) 存储，key = ws:pending:{user_id}。
connect 时取出并投递，投递成功即删除。
"""

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_CLIENTS: dict[int, aioredis.Redis] = {}
_MAX_CONNECTIONS = 10

TTL_SECONDS = 3600  # 待发消息 1 小时后过期


def _get_client() -> aioredis.Redis:
    """获取 PID 隔离的 Redis 客户端。"""
    from django.conf import settings

    pid = os.getpid()
    if pid not in _CLIENTS:
        url = f"{settings.REDIS_URL}/{settings.REDIS_DB_WS_PENDING}"
        _CLIENTS[pid] = aioredis.from_url(
            url,
            max_connections=_MAX_CONNECTIONS,
            decode_responses=True,
        )
    return _CLIENTS[pid]


def _key(user_id: str) -> str:
    return f"ws:pending:{user_id}"


async def store(user_id: str, message: dict) -> None:
    """存储一条待发消息。"""
    try:
        r = _get_client()
        k = _key(user_id)
        pipe = r.pipeline()
        pipe.rpush(k, json.dumps(message, ensure_ascii=False))
        pipe.expire(k, TTL_SECONDS)
        await pipe.execute()
    except Exception:
        logger.warning("[WSPending] store failed for user %s", user_id, exc_info=True)


async def drain(user_id: str) -> list[dict]:
    """取出并删除用户的所有待发消息，按入队顺序返回。"""
    try:
        r = _get_client()
        k = _key(user_id)
        pipe = r.pipeline()
        pipe.lrange(k, 0, -1)
        pipe.delete(k)
        results, _ = await pipe.execute()
        if results:
            return [json.loads(m) for m in results]
        return []
    except Exception:
        logger.warning("[WSPending] drain failed for user %s", user_id, exc_info=True)
        return []
