import os
import asyncio
import logging

# MUST be set before any Django-dependent imports
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

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
logger = logging.getLogger(__name__)


class LifespanHandler:
    """处理 ASGI lifespan 事件，启动/停止后台消费者和 Telegram Channel"""

    def __init__(self):
        pass

    async def __call__(self, scope, receive, send):
        global _consumer, _telegram_channel

        # 确保 scope 类型是 lifespan
        assert scope['type'] == 'lifespan'

        try:
            while True:
                event = await receive()

                if event['type'] == 'lifespan.startup':
                    try:
                        # 启动 AgentTaskConsumer
                        from apps.agent.consumer import AgentTaskConsumer
                        _consumer = AgentTaskConsumer(concurrency=4)
                        await _consumer.start()
                        logger.info('[ASGI] AgentTaskConsumer started')

                        # 恢复并自动重启之前运行的框架
                        await self._restore_frames()

                        # 启动 TelegramChannel
                        await self._start_telegram_channel()

                        await send({'type': 'lifespan.startup.complete'})
                        logger.info('[ASGI] Lifespan startup complete')
                    except Exception as e:
                        logger.error(f'[ASGI] Startup error: {e}')
                        await send({'type': 'lifespan.startup.failed', 'reason': str(e)})

                elif event['type'] == 'lifespan.shutdown':
                    try:
                        # 停止 TelegramChannel
                        if _telegram_channel:
                            await _telegram_channel.stop()
                            logger.info('[ASGI] TelegramChannel stopped')

                        # 停止 AgentTaskConsumer
                        if _consumer:
                            await _consumer.stop()
                            logger.info('[ASGI] AgentTaskConsumer stopped')

                        await send({'type': 'lifespan.shutdown.complete'})
                        logger.info('[ASGI] Lifespan shutdown complete')
                    except Exception as e:
                        logger.error(f'[ASGI] Shutdown error: {e}')
                        await send({'type': 'lifespan.shutdown.failed', 'reason': str(e)})
                    return

        except Exception as e:
            logger.error(f'[ASGI] Lifespan handler error: {e}')

    async def _start_telegram_channel(self):
        global _telegram_channel
        from django.conf import settings
        from apps.channel.telegram import TelegramChannel
        from apps.agent.supervisor import SupervisorAgent

        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        if not token:
            logger.warning('[ASGI] TELEGRAM_BOT_TOKEN not set, skipping TelegramChannel')
            return

        try:
            supervisor = SupervisorAgent.get_instance()
            _telegram_channel = TelegramChannel(token=token, supervisor_agent=supervisor)
            await _telegram_channel.start()
            logger.info('[ASGI] TelegramChannel started successfully')
        except Exception as e:
            logger.error(f'[ASGI] Failed to start TelegramChannel: {e}')

    async def _restore_frames(self):
        """恢复并自动重启之前运行的框架。"""
        try:
            from apps.agent.frame_manager import FrameManager
            fm = FrameManager.get_instance()
            await fm.restore_and_restart_frames()
        except Exception as e:
            logger.warning('[ASGI] Frame restore failed: %s', e)


# 创建 ASGI 应用程序，确保包含所有协议处理器
# 创建 LifespanHandler 的实例
lifespan_handler = LifespanHandler()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": TokenAuthMiddleware(
        URLRouter(
            notify_routing.websocket_urlpatterns
        )
    ),
    "lifespan": lifespan_handler,
})
