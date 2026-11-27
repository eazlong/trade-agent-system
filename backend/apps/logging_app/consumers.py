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
# 停读流任务的最长等待（秒）。超时就放弃等待、继续关连接池：
# 卡住的协程不该拖住 FD 回收。
_READER_STOP_TIMEOUT = 5.0
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

    async def _stop_reader(self):
        """停掉读流任务（取消后台协程，等它真的退出）。

        带超时兜底：reader 若卡在不可取消的 await（send 背压、xread 建连），
        无超时的 await 会把 disconnect() 一起挂住，_close_redis 永远不执行，
        FD 也就永远回收不了 —— 那正是这次要修的症状。
        """
        if self._read_task:
            task = self._read_task
            self._read_task = None
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=_READER_STOP_TIMEOUT)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    async def _close_redis(self):
        """关闭并清空 Redis 客户端。

        必须把 self._redis 置回 None：只 aclose 不置空的话，下一次 subscribe
        会看到一个「已关闭但非空」的对象，既不会重连也不会再关一次，
        等于把旧连接池永久挂在 self 上。
        """
        client = self._redis
        self._redis = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                # aclose 失败意味着池里的 socket 还挂着 —— 再退一步直接断池，
                # 保证 FD 一定被还回去，而不是只留一条 warning。
                logger.warning("[LogWebSocket] redis aclose failed", exc_info=True)
                pool = getattr(client, "connection_pool", None)
                if pool is not None:
                    try:
                        await pool.disconnect()
                    except Exception:
                        logger.warning(
                            "[LogWebSocket] redis pool disconnect failed", exc_info=True
                        )

    async def disconnect(self, close_code):
        await self._stop_reader()
        await self._close_redis()
        logger.info(f"[LogWebSocket] User {self.user_id} disconnected")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(json.dumps({"error": "Invalid JSON"}))
            return

        message_type = data.get("type")

        if message_type == "ping":
            # 应用层心跳应答。LogConsumer 原本只处理 subscribe/unsubscribe，
            # 前端的 ping 落进 else 分支连回复都没有 —— 前端就无法判断
            # 「连接还在」还是「连接已经半死待 TCP 超时」，只能挂到 fd 被回收。
            await self.send(json.dumps({"type": "pong"}))
            return

        if message_type == "subscribe":
            # 先停掉上一次的读流任务，避免两个 reader 同时往同一个 socket 写
            await self._stop_reader()

            # Parse filter parameters
            level = data.get("level", "").upper()
            self._min_level_rank = LEVEL_ORDER.get(level, 0)
            self._module_filter = data.get("module") or None
            self._search_filter = data.get("search") or None

            # 复用已有连接池：改筛选条件时前端会重发 subscribe，这时只是
            # 换过滤参数，没必要拆掉再建一个客户端 —— 重建期间产生的日志会
            # 丢，且每次 topic 变更都多一次 TCP 握手。
            if self._redis is None:
                self._redis = aioredis.from_url(
                    getattr(settings, "REDIS_URL", "redis://localhost:6379"),
                    decode_responses=True,
                )

            # Start background Redis Stream reader。把客户端作为参数传进去，
            # reader 内部不再读 self._redis —— 否则断开时池已被置 None，
            # 正在跑的 reader 会抛 NoneType.xread。
            self._read_task = asyncio.create_task(self._read_stream(self._redis))

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
            # 取消订阅要把 Redis 连接一起还回去：读流停了但连接池留着，
            # 等于每次「取订 → 再订」都净增一条连接到 Redis。
            await self._stop_reader()
            await self._close_redis()

    async def _read_stream(self, client):
        """Continuously read from Redis Stream and push matching entries."""
        try:
            last_id = "$"  # Start from latest, don't replay history

            while True:
                try:
                    messages = await client.xread(
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
            # 只清「自己还是当前任务」的情况：重订阅时新 reader 已把
            # self._read_task 换成自己，旧 reader 退出时不能把新的抹掉，
            # 否则 disconnect 会漏掉取消，读流协程与连接一起泄漏。
            if self._read_task is asyncio.current_task():
                self._read_task = None
