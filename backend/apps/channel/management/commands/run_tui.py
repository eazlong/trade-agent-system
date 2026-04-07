"""
run_tui.py — 启动 TUI Channel

用法:
    python manage.py run_tui
"""
import asyncio
import logging
import os
import sys

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '启动 TUI Channel（终端交互界面）'

    def handle(self, *args, **options):
        # 设置日志
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        )

        # 设置环境变量（如果未设置）
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

        # 确保事件循环
        try:
            loop = asyncio.get_running_loop()
            self.stderr.write(self.style.WARNING('已在事件循环中运行，请使用 asyncio.run()'))
            return
        except RuntimeError:
            pass

        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS('\nTUI 已退出'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'TUI 启动失败: {e}'))
            raise

    async def _run(self):
        from apps.channel.tui import TUIChannel
        from apps.agent.supervisor import SupervisorAgent

        # 初始化 SupervisorAgent（单例）
        supervisor = SupervisorAgent.get_instance()

        # 启动 TUI
        channel = TUIChannel(supervisor_agent=supervisor)
        await channel.start()
