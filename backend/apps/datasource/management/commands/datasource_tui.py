"""
数据源管理命令
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '启动数据源 TUI 监控界面'

    def handle(self, *args, **options):
        from apps.datasource.tui import main
        main()