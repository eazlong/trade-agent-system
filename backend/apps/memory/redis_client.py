"""Redis connection pool with PID-based isolation for Celery fork safety."""

import asyncio
import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_CLIENTS: dict[int, aioredis.Redis] = {}
_MAX_CONNECTIONS = 20


class RedisPool:
    """PID-isolated Redis connection pool.

    Each process (identified by PID) gets its own Redis client instance.
    This prevents connection sharing issues when Celery forks workers.
    """

    @staticmethod
    def get_client() -> aioredis.Redis:
        """Get or create a Redis client for the current process."""
        from django.conf import settings

        pid = os.getpid()
        if pid not in _CLIENTS:
            url = getattr(settings, "REDIS_MEMORY_URL", None)
            if not url:
                url = settings.REDIS_URL + "/6"
            _CLIENTS[pid] = aioredis.from_url(
                url,
                max_connections=_MAX_CONNECTIONS,
                decode_responses=True,
            )
            logger.debug("[RedisPool] Created client for PID %d", pid)
        return _CLIENTS[pid]

    @staticmethod
    def close_client() -> None:
        """Close the Redis client for the current process."""
        pid = os.getpid()
        client = _CLIENTS.pop(pid, None)
        if client:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(client.aclose())
                else:
                    loop.run_until_complete(client.aclose())
            except Exception as e:
                logger.warning(
                    "[RedisPool] Failed to close client for PID %d: %s", pid, e
                )
