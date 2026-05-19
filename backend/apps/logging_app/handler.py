"""
Custom logging handler that writes logs to:
1. Django DB (SystemLog model) — async, non-blocking
2. Redis Stream — for real-time SSE streaming
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

import redis

from .mapper import resolve_module

_redis_client = None
_redis_lock = threading.Lock()


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is None:
            url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            _redis_client = redis.Redis.from_url(url, decode_responses=True)
        return _redis_client


# Stream key for real-time log broadcasting
LOG_STREAM_KEY = "system:logs"
# Max stream length (keep last 10000 entries)
LOG_STREAM_MAXLEN = 10000


class SystemLogHandler(logging.Handler):
    """
    Logging handler that captures all Python logging output and stores it
    in the SystemLog DB model + pushes to Redis Stream for real-time streaming.
    """

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            module = resolve_module(record.name)
            level_name = record.levelname
            trace_id = getattr(record, "trace_id", "")
            extra_data = {}
            # Collect extra fields from the log record
            for key in (
                "request_id",
                "user_id",
                "task_id",
                "duration_ms",
                "token_used",
            ):
                val = getattr(record, key, None)
                if val is not None:
                    extra_data[key] = val

            entry = {
                "level": level_name,
                "module": module,
                "logger": record.name,
                "message": msg,
                "trace_id": trace_id,
                "extra": json.dumps(extra_data),
                "ts": datetime.now(timezone.utc).isoformat(),
            }

            # Push to Redis Stream (non-blocking, fire-and-forget)
            try:
                r = _get_redis()
                r.xadd(
                    LOG_STREAM_KEY, entry, maxlen=LOG_STREAM_MAXLEN, approximate=True
                )
            except Exception:
                pass  # Redis unavailable — don't block the app

            # Write to DB asynchronously (best-effort, non-blocking)
            if not getattr(record, "_suppress_db", False):
                threading.Thread(
                    target=self._save_to_db,
                    args=(level_name, module, record.name, msg, trace_id, extra_data),
                    daemon=True,
                ).start()

        except Exception:
            # Never let logging failures break the application
            self.handleError(record)

    @staticmethod
    def _save_to_db(level_name, module, logger_name, msg, trace_id, extra_data):
        try:
            # Lazy import to avoid AppRegistryNotReady at module load time
            from apps.logging_app.models import SystemLog

            SystemLog.objects.create(
                level=level_name,
                module=module,
                logger_name=logger_name,
                message=msg[:4000],  # Cap message length
                trace_id=trace_id,
                extra_data=extra_data,
            )
        except Exception:
            pass  # DB write failure should not surface


class DBOnlyLogHandler(logging.Handler):
    """Lightweight handler that only writes to DB (no Redis)."""

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            module = resolve_module(record.name)
            SystemLog = self._get_model()
            if SystemLog is not None:
                SystemLog.objects.create(
                    level=record.levelname,
                    module=module,
                    logger_name=record.name,
                    message=msg[:4000],
                    trace_id=getattr(record, "trace_id", ""),
                    extra_data={},
                )
        except Exception:
            pass

    @staticmethod
    def _get_model():
        try:
            from apps.logging_app.models import SystemLog

            return SystemLog
        except Exception:
            return None
