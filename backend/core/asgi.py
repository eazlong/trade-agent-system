import os
import asyncio
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


def get_telegram_channel():
    """返回进程内 TelegramChannel 实例（lifespan 启动时创建）。

    供 apps.agent.reply_fanout 推送主通道副本时复用，避免重复实例化。
    """
    return _telegram_channel


async def _listen_progress_notifications():
    """Background task: subscribe to Redis pubsub and forward to active channel."""
    import json

    import redis.asyncio as aioredis

    _PROGRESS_CHANNEL = "task:progress:notifications"
    from django.conf import settings

    url = settings.REDIS_URL
    if url.rsplit("/", 1)[-1].isdigit():
        url = url.rsplit("/", 1)[0] + "/3"

    reconnect_delay = 1  # 初始重连延迟（秒）
    max_reconnect_delay = 60  # 最大重连延迟

    while True:
        pubsub = None
        try:
            r = aioredis.from_url(url, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(_PROGRESS_CHANNEL)
            logger.info('[ASGI] Progress notification listener started')
            reconnect_delay = 1  # 连接成功，重置延迟

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
            break
        except Exception:
            logger.error("[ASGI] progress listener crashed, reconnecting in %ds", reconnect_delay, exc_info=True)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)  # 指数退避
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe(_PROGRESS_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass


async def _route_notification(user_id: str, text: str):
    """Route a notification to BOTH the web (WS group + ws_pending drain) and
    the user's main channel (Telegram/Lark). Multi-channel — not mutually exclusive.

    Shared fan-out primitives live in apps.agent.reply_fanout, so the same
    delivery logic is reused by agent reply fan-out (fan_out_reply).
    """
    from apps.agent.reply_fanout import push_main_channel, push_web

    # Web 兜底：所有任务完成通知都先尝试推到 chat_ws group。
    # ChatConsumer 已加入 user_{user_id}；group_send 命中即发，否则回落到 ws_pending。
    # 支持 open_id / telegram_id → Django UUID 解析，飞书发起的会话也能同步到 web。
    await push_web(user_id, text)

    # 主通道（MAIN_CHANNEL=lark 默认）：独立分支，多通道不互斥
    await push_main_channel(user_id, text)


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
