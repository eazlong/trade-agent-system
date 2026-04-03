import os
import asyncio
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Start the application with background services'

    def handle(self, *args, **options):
        logger.info("Starting application with background services...")

        # 启动 AgentTaskConsumer
        self.stdout.write("Starting AgentTaskConsumer...")
        from apps.agent.consumer import AgentTaskConsumer
        consumer = AgentTaskConsumer(concurrency=4)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def start_services():
            await consumer.start()
            logger.info('AgentTaskConsumer started')

            # 启动 TelegramChannel
            token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
            if token:
                from apps.channel.telegram import TelegramChannel
                from apps.agent.supervisor import SupervisorAgent

                supervisor = SupervisorAgent.get_instance()
                telegram_channel = TelegramChannel(token=token, supervisor_agent=supervisor)

                await telegram_channel.start()
                logger.info('TelegramChannel started')

                # 保持服务运行
                try:
                    # 等待中断信号
                    while True:
                        await asyncio.sleep(1)
                except KeyboardInterrupt:
                    logger.info('Shutting down services...')

                    await telegram_channel.stop()
                    await consumer.stop()

                    logger.info('Services stopped')
            else:
                logger.warning('TELEGRAM_BOT_TOKEN not set, skipping TelegramChannel')

                # 保持服务运行
                try:
                    # 等待中断信号
                    while True:
                        await asyncio.sleep(1)
                except KeyboardInterrupt:
                    logger.info('Shutting down services...')
                    await consumer.stop()

                    logger.info('Services stopped')

        try:
            loop.run_until_complete(start_services())
        except KeyboardInterrupt:
            logger.info("Application interrupted by user")
        finally:
            loop.close()