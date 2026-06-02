import os
import asyncio
import json
import logging

# MUST be set before any Django-dependent imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")

import django

django.setup()

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from apps.notify.middleware import TokenAuthMiddleware

# 导入 WebSocket 路由
from apps.notify import routing as notify_routing

# 获取基础 Django ASGI 应用
django_asgi_app = get_asgi_application()

# 全局变量用于存储消费者和 Telegram Channel 实例
_consumer = None
_telegram_channel = None
_progress_listener_task = None
_dlq_consumer = None
logger = logging.getLogger(__name__)


async def _listen_progress_notifications():
    """Background task: subscribe to Redis pubsub and forward to active channel."""
    import json

    import redis.asyncio as aioredis

    _PROGRESS_CHANNEL = "task:progress:notifications"
    from django.conf import settings

    url = settings.REDIS_URL
    if url.rsplit("/", 1)[-1].isdigit():
        url = url.rsplit("/", 1)[0] + "/3"

    pubsub = None
    try:
        r = aioredis.from_url(url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(_PROGRESS_CHANNEL)
        logger.info('[ASGI] Progress notification listener started')

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                text = data.get("text", "")
                user_id = data.get("user_id", "")
                if not text:
                    continue
                await _route_notification(user_id, text)
            except Exception:
                logger.warning("[ASGI] failed to process progress notification", exc_info=True)
    except (asyncio.CancelledError, GeneratorExit, RuntimeError):
        pass
    except Exception:
        logger.error("[ASGI] progress listener crashed", exc_info=True)
    finally:
        if pubsub:
            try:
                await pubsub.unsubscribe(_PROGRESS_CHANNEL)
                await pubsub.aclose()
            except Exception:
                pass


async def _route_notification(user_id: str, text: str):
    """Route a notification to the user's active channel (Telegram or Lark)."""
    from channels.db import database_sync_to_async
    from apps.channel.channel_resolver import get_user_active_channel

    target = await database_sync_to_async(get_user_active_channel)(user_id)

    if target is None:
        # Fallback: try Telegram in-memory instance (legacy behavior)
        if _telegram_channel and _telegram_channel._app:
            await _telegram_channel.send_message(text)
        else:
            logger.debug("[ASGI] no active channel for user %s, dropping: %s", user_id, text[:50])
        return

    if target.channel_type == "telegram":
        if _telegram_channel and _telegram_channel._app:
            old_chat_id = _telegram_channel._chat_id
            try:
                _telegram_channel._chat_id = int(target.chat_id)
                await _telegram_channel.send_message(text)
            finally:
                _telegram_channel._chat_id = old_chat_id
        else:
            logger.debug("[ASGI] Telegram not ready, dropping notification: %s", text[:50])

    elif target.channel_type == "lark":
        try:
            from apps.channel.lark_ws import _get_channel as _get_lark_channel
            lark = _get_lark_channel()
            await lark.send_message_to_user(target.user_open_id, text)
        except Exception:
            logger.warning("[ASGI] failed to send Lark notification", exc_info=True)


class LifespanHandler:
    """处理 ASGI lifespan 事件，启动/停止后台消费者和 Telegram Channel"""

    def __init__(self):
        pass

    async def __call__(self, scope, receive, send):
        global _consumer, _telegram_channel, _progress_listener_task, _dlq_consumer

        # 确保 scope 类型是 lifespan
        assert scope["type"] == "lifespan"

        try:
            while True:
                event = await receive()

                if event["type"] == "lifespan.startup":
                    try:
                        # 启动 AgentTaskConsumer
                        from apps.agent.consumer import AgentTaskConsumer

                        _consumer = AgentTaskConsumer(concurrency=4)
                        await _consumer.start()
                        logger.info("[ASGI] AgentTaskConsumer started")

                        # 恢复并自动重启之前运行的框架
                        await self._restore_frames()

                        # 启动 DeadLetterConsumer
                        from apps.agent.dlq_consumer import DeadLetterConsumer

                        _dlq_consumer = DeadLetterConsumer(block_ms=10000)
                        await _dlq_consumer.start()
                        logger.info("[ASGI] DeadLetterConsumer started")

                        # 启动 TelegramChannel
                        await self._start_telegram_channel()

                        # 启动进度通知监听器
                        _progress_listener_task = asyncio.create_task(
                            _listen_progress_notifications()
                        )

                        await send({"type": "lifespan.startup.complete"})
                        logger.info("[ASGI] Lifespan startup complete")
                    except Exception as e:
                        logger.error(f"[ASGI] Startup error: {e}")
                        await send(
                            {"type": "lifespan.startup.failed", "reason": str(e)}
                        )

                elif event["type"] == "lifespan.shutdown":
                    try:
                        # 停止进度通知监听器
                        if _progress_listener_task:
                            _progress_listener_task.cancel()
                            try:
                                await _progress_listener_task
                            except (asyncio.CancelledError, RuntimeError):
                                pass

                        # 停止 TelegramChannel
                        if _telegram_channel:
                            await _telegram_channel.stop()
                            logger.info("[ASGI] TelegramChannel stopped")

                        # 停止 DeadLetterConsumer
                        if _dlq_consumer:
                            await _dlq_consumer.stop()
                            logger.info("[ASGI] DeadLetterConsumer stopped")

                        # 停止 AgentTaskConsumer
                        if _consumer:
                            await _consumer.stop()
                            logger.info("[ASGI] AgentTaskConsumer stopped")

                        await send({"type": "lifespan.shutdown.complete"})
                        logger.info("[ASGI] Lifespan shutdown complete")
                    except Exception as e:
                        logger.error(f"[ASGI] Shutdown error: {e}")
                        await send(
                            {"type": "lifespan.shutdown.failed", "reason": str(e)}
                        )
                    return

        except Exception as e:
            logger.error(f"[ASGI] Lifespan handler error: {e}")

    async def _start_telegram_channel(self):
        global _telegram_channel
        from django.conf import settings
        from apps.channel.telegram import TelegramChannel
        from apps.agent.supervisor import SupervisorAgent

        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if not token:
            logger.warning(
                "[ASGI] TELEGRAM_BOT_TOKEN not set, skipping TelegramChannel"
            )
            return

        try:
            supervisor = SupervisorAgent.get_instance()
            _telegram_channel = TelegramChannel(
                token=token, supervisor_agent=supervisor
            )
            await _telegram_channel.start()
            logger.info("[ASGI] TelegramChannel started successfully")
        except Exception as e:
            logger.error(f"[ASGI] Failed to start TelegramChannel: {e}")

    async def _restore_frames(self):
        """恢复并自动重启之前运行的框架。"""
        try:
            from apps.agent.frame_manager import FrameManager

            fm = FrameManager.get_instance()
            await fm.restore_and_restart_frames()
        except Exception as e:
            logger.warning("[ASGI] Frame restore failed: %s", e)


# 创建 ASGI 应用程序，确保包含所有协议处理器
# 创建 LifespanHandler 的实例
lifespan_handler = LifespanHandler()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": TokenAuthMiddleware(
            URLRouter(notify_routing.websocket_urlpatterns)
        ),
        "lifespan": lifespan_handler,
    }
)
