"""
WebSocket consumer for real-time log streaming.
Reads from Redis Stream 'system:logs' and pushes to connected clients,
with optional level/module/search filtering.

Auth: JWT token passed via query parameter '?token=xxx',
validated directly in the consumer.
"""

import asyncio
import json
import logging
from urllib.parse import parse_qs

import redis.asyncio as aioredis
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)

LOG_STREAM_KEY = "system:logs"
LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _get_user_by_token(token_string):
    """Validate JWT and return user. Must run via sync_to_async."""
    from django.contrib.auth import get_user_model

    try:
        access_token = AccessToken(token_string)
        user_id = access_token["user_id"]
    except (TokenError, KeyError) as e:
        logger.warning(f"[LogWebSocket] Token decode error: {e}")
        return None

    User = get_user_model()
    try:
        return User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        logger.warning(f"[LogWebSocket] User {user_id} not found or inactive")
        return None
    except Exception as e:
        logger.warning(f"[LogWebSocket] User lookup error: {e}")
        return None


class LogConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time log delivery.

    Client sends:
        {"type": "subscribe", "level": "WARNING", "module": "trading", "search": "error"}

    Server pushes:
        {"type": "log_entry", "id": "...", "level": "ERROR", "module": "trading",
         "message": "...", "trace_id": "...", "extra_data": {}, "created_at": "..."}
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._read_task = None
        self._redis = None
        self._min_level_rank = 0
        self._module_filter = None
        self._search_filter = None
        self.user_id = None

    async def connect(self):
        # Parse query string for JWT token
        query_string = self.scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        token_list = query_params.get("token", [])

        if not token_list:
            logger.warning("[LogWebSocket] No token in query string")
            await self.close(code=4001)
            return

        token = token_list[0]
        user = await sync_to_async(_get_user_by_token)(token)

        if not user:
            logger.warning(
                "[LogWebSocket] Auth failed: invalid token or user not found"
            )
            await self.close(code=4001)
            return

        self.user_id = user.id
        await self.accept()
        logger.info(f"[LogWebSocket] User {user.id} connected")

    async def disconnect(self, close_code):
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._redis:
            await self._redis.aclose()
        logger.info(f"[LogWebSocket] User {self.user_id} disconnected")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(json.dumps({"error": "Invalid JSON"}))
            return

        message_type = data.get("type")

        if message_type == "subscribe":
            # Cancel any existing stream reader
            if self._read_task:
                self._read_task.cancel()
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    pass

            # Parse filter parameters
            level = data.get("level", "").upper()
            self._min_level_rank = LEVEL_ORDER.get(level, 0)
            self._module_filter = data.get("module") or None
            self._search_filter = data.get("search") or None

            # Initialize async Redis client
            self._redis = aioredis.from_url(
                getattr(settings, "REDIS_URL", "redis://localhost:6379"),
                decode_responses=True,
            )

            # Start background Redis Stream reader
            self._read_task = asyncio.create_task(self._read_stream())

            await self.send(
                json.dumps(
                    {
                        "type": "subscribed",
                        "level": level,
                        "module": self._module_filter,
                    }
                )
            )

        elif message_type == "unsubscribe":
            if self._read_task:
                self._read_task.cancel()
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    pass
            self._read_task = None

    async def _read_stream(self):
        """Continuously read from Redis Stream and push matching entries."""
        try:
            last_id = "$"  # Start from latest, don't replay history

            while True:
                try:
                    messages = await self._redis.xread(
                        {LOG_STREAM_KEY: last_id},
                        count=50,
                        block=1000,
                    )
                except aioredis.ConnectionError:
                    await asyncio.sleep(1)
                    continue

                if not messages:
                    continue

                for _stream, entries in messages:
                    for entry_id, fields in entries:
                        last_id = entry_id

                        # Apply level filter
                        level_rank = LEVEL_ORDER.get(fields.get("level", "INFO"), 1)
                        if level_rank < self._min_level_rank:
                            continue

                        # Apply module filter
                        if self._module_filter:
                            if fields.get("module", "") != self._module_filter:
                                continue

                        # Apply search filter
                        if self._search_filter:
                            message = fields.get("message", "")
                            if self._search_filter.lower() not in message.lower():
                                continue

                        # Build log entry matching SystemLog interface
                        extra = fields.get("extra", "{}")
                        try:
                            extra_data = (
                                json.loads(extra) if isinstance(extra, str) else extra
                            )
                        except Exception:
                            extra_data = {}

                        log_entry = {
                            "type": "log_entry",
                            "id": entry_id,
                            "level": fields.get("level", "INFO"),
                            "module": fields.get("module", "unknown"),
                            "message": fields.get("message", ""),
                            "trace_id": fields.get("trace_id", ""),
                            "extra_data": extra_data,
                            "created_at": fields.get("ts", ""),
                        }

                        await self.send(json.dumps(log_entry))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[LogWebSocket] Stream reader error: {e}")
        finally:
            self._read_task = None
