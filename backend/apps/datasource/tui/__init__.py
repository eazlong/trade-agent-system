"""
数据源 TUI - 终端用户界面

用于监控和管理数据源的交互式终端界面。
"""
import asyncio
import time
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.prompt import Prompt, Confirm

console = Console()


class DataSourceTUI:
    """
    数据源 TUI 主类

    功能：
    - 数据源状态监控
    - 数据流实时显示
    - 订阅管理
    - 质量报告查看
    """

    def __init__(self):
        """初始化 TUI"""
        self.running = False
        self.selected_source: Optional[str] = None
        self.auto_refresh = True
        self.refresh_interval = 2.0

    def create_header(self) -> Panel:
        """创建头部面板"""
        title = Text("📊 数据源监控系统", style="bold cyan")
        subtitle = Text(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
        content = Text.assemble(title, "\n", subtitle)
        return Panel(content, style="bold blue")

    def create_sources_table(self) -> Table:
        """创建数据源状态表格"""
        table = Table(title="📡 数据源状态", show_header=True, header_style="bold magenta")
        table.add_column("数据源", style="cyan", width=12)
        table.add_column("状态", width=12)
        table.add_column("订阅数", justify="right", width=8)
        table.add_column("最后数据", width=16)

        try:
            from apps.datasource.registry import DataSourceRegistry

            sources = DataSourceRegistry.list_registered()

            for name in sources:
                is_loaded = DataSourceRegistry.is_loaded(name)

                if is_loaded:
                    source = DataSourceRegistry.get(name)
                    status = source.get_ws_status().value
                    sub_count = source.get_subscription_count()
                    last_data = source.get_last_data_time()
                    last_data_str = f"{int(time.time() - last_data)}秒前" if last_data > 0 else "-"
                else:
                    status = "未加载"
                    sub_count = 0
                    last_data_str = "-"

                # 根据状态设置颜色
                status_style = {
                    'connected': 'green',
                    'connecting': 'yellow',
                    'disconnected': 'red',
                    'error': 'bold red',
                    '未加载': 'dim'
                }.get(status, 'white')

                table.add_row(
                    name,
                    Text(status, style=status_style),
                    str(sub_count),
                    last_data_str
                )

        except Exception as e:
            table.add_row("错误", str(e), "-", "-")

        return table

    def create_storage_table(self) -> Table:
        """创建数据存储统计表格"""
        table = Table(title="💾 数据存储统计", show_header=True, header_style="bold green")
        table.add_column("数据类型", style="cyan", width=12)
        table.add_column("交易对数", justify="right", width=10)
        table.add_column("数据条数", justify="right", width=12)

        try:
            from apps.datasource.store import get_data_store
            store = get_data_store()
            stats = store.get_stats()

            for data_type, count in stats.get('symbols_per_type', {}).items():
                total = store.get_count(data_type)
                table.add_row(data_type, str(count), str(total))

            # 总计
            table.add_section()
            table.add_row(
                "[bold]总计[/]",
                "-",
                f"[bold]{stats['total_entries']}[/]"
            )

        except Exception as e:
            table.add_row("错误", str(e), "-")

        return table

    def create_quality_table(self) -> Table:
        """创建数据质量表格"""
        table = Table(title="📈 数据质量监控", show_header=True, header_style="bold yellow")
        table.add_column("数据源", style="cyan", width=10)
        table.add_column("交易对", width=12)
        table.add_column("类型", width=8)
        table.add_column("完整率", justify="right", width=10)
        table.add_column("延迟(ms)", justify="right", width=10)
        table.add_column("状态", width=10)

        try:
            from apps.datasource.monitor import get_quality_monitor
            monitor = get_quality_monitor()
            reports = monitor.get_all_reports()

            for source, source_reports in reports.items():
                for symbol, symbol_reports in source_reports.items():
                    for data_type, report in symbol_reports.items():
                        status_style = {
                            'good': 'green',
                            'warning': 'yellow',
                            'critical': 'red',
                            'unknown': 'dim'
                        }.get(report.status, 'white')

                        table.add_row(
                            source,
                            symbol,
                            data_type,
                            f"{report.completeness_rate * 100:.1f}%",
                            f"{report.avg_latency_ms:.1f}",
                            Text(report.status, style=status_style)
                        )

            if not reports:
                table.add_row("-", "-", "-", "-", "-", "-")

        except Exception as e:
            table.add_row("错误", str(e), "-", "-", "-", "-")

        return table

    def create_menu(self) -> Panel:
        """创建菜单面板"""
        menu_text = """
[bold cyan]操作菜单[/]

[yellow]1.[/] 查看数据源详情
[yellow]2.[/] 连接数据源
[yellow]3.[/] 断开数据源
[yellow]4.[/] 查看订阅列表
[yellow]5.[/] 添加订阅
[yellow]6.[/] 触发质量检查
[yellow]r.[/] 刷新
[yellow]q.[/] 退出
"""
        return Panel(menu_text, title="控制面板", style="bold cyan")

    def create_layout(self) -> Layout:
        """创建布局"""
        layout = Layout()

        layout.split(
            Layout(name="header", size=4),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=8)
        )

        layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1)
        )

        layout["header"].update(self.create_header())
        layout["left"].update(self.create_sources_table())
        layout["right"].update(self.create_storage_table())
        layout["footer"].update(self.create_menu())

        return layout

    def refresh(self) -> Layout:
        """刷新显示"""
        return self.create_layout()

    async def run_async(self) -> None:
        """异步运行 TUI"""
        self.running = True

        with Live(console=console, refresh_per_second=0.5) as live:
            while self.running:
                try:
                    live.update(self.refresh())
                    await asyncio.sleep(self.refresh_interval)
                except KeyboardInterrupt:
                    self.running = False
                    break

    def run(self) -> None:
        """运行 TUI"""
        console.clear()
        console.print("[bold cyan]正在启动数据源 TUI...[/]\n")

        # 检查模块
        try:
            from apps.datasource.registry import DataSourceRegistry
            from apps.datasource.store import get_data_store
            from apps.datasource.monitor import get_quality_monitor

            console.print("[green]✓ 数据源模块加载成功[/]")
            console.print(f"[dim]已注册数据源: {DataSourceRegistry.list_registered()}[/]\n")

        except Exception as e:
            console.print(f"[red]✗ 数据源模块加载失败: {e}[/]")
            return

        # 运行主界面
        asyncio.run(self.run_async())


def main():
    """主入口"""
    tui = DataSourceTUI()
    tui.run()


if __name__ == "__main__":
    main()