from __future__ import annotations
import asyncio
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Start Telegram Bot channel'

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.stderr.write('TELEGRAM_BOT_TOKEN is not set in settings/env')
            return

        from apps.channel.telegram import TelegramChannel
        from apps.agent.supervisor import SupervisorAgent

        supervisor = SupervisorAgent.get_instance()
        channel = TelegramChannel(token=token, supervisor_agent=supervisor)

        self.stdout.write('Starting Telegram channel...')
        asyncio.run(self._run(channel))

    async def _run(self, channel):
        await channel.start()
        # Block until interrupted
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await channel.stop()
