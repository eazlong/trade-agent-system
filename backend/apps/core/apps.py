import os
import threading
import asyncio
import logging
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    def ready(self):
        # 避免重复初始化（Django 会运行两次）
        if os.environ.get("RUN_MAIN") != "true":
            return

        logger.info("Core app ready, initializing background services...")

        # 启动后台服务线程
        thread = threading.Thread(target=self._start_background_services, daemon=True)
        thread.start()

    def _start_background_services(self):
        """
        在单独线程中启动后台服务
        """
        import django
        from django.conf import settings

        # 设置 Django 环境
        if not settings.configured:
            django.setup()

        try:
            # 获取或创建事件循环
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # 运行异步初始化
            loop.run_until_complete(self._async_init_services())

        except Exception as e:
            logger.error(f"Error starting background services: {e}")

    async def _async_init_services(self):
        """
        异步初始化服务
        """
        logger.info("Starting background services asynchronously...")

        # 启动 AgentTaskConsumer
        from apps.agent.consumer import AgentTaskConsumer

        consumer = AgentTaskConsumer(concurrency=4)
        await consumer.start()
        logger.info("AgentTaskConsumer started")

        # 启动 TelegramChannel
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if token:
            from apps.channel.telegram import TelegramChannel
            from apps.agent.supervisor import SupervisorAgent

            supervisor = SupervisorAgent.get_instance()
            telegram_channel = TelegramChannel(token=token, supervisor_agent=supervisor)

            await telegram_channel.start()
            logger.info("TelegramChannel started")

            # 保存引用以便稍后清理
            self.telegram_channel = telegram_channel

        else:
            logger.warning("TELEGRAM_BOT_TOKEN not set, skipping TelegramChannel")
