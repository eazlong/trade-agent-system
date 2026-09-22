"""回填 / 续拉 BTC 日线（原始 OHLCV）。

日频增量由判定任务自带（第①段后续单元），这个命令是**人工可控的回填入口**：
冷启动、补历史、以及算法调整后需要更长窗口时用它。

幂等：重复运行不产生重复行，也不改写未变化的行（报告里的「未变」计数即此）。
失败以非零退出码结束，不把「拉取失败」混进「今天没数据」。
"""

from __future__ import annotations

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from apps.regime import config
from apps.regime.candles import CandleFetchError, sync_daily_candles


def _parse_date(value: str | None, flag: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise CommandError(f"{flag} 需要 YYYY-MM-DD 格式，收到：{value!r}")


class Command(BaseCommand):
    help = "拉取并落库 BTC 日线（原始 OHLCV）；幂等，可反复运行"

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            default=config.CANDLES.symbol,
            help=f"标的（ccxt 格式），默认 {config.CANDLES.symbol}",
        )
        parser.add_argument(
            "--start",
            help="回填起点 YYYY-MM-DD（含）。省略则从库里最后一根 + 1 天续拉；表为空时必填",
        )
        parser.add_argument(
            "--end",
            help="回填终点 YYYY-MM-DD（含）。默认 = 最后已收盘的自然日",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只拉取与比对，不写库（用于确认回填窗口与数据形状）",
        )

    def handle(self, *args, **options):
        start = _parse_date(options["start"], "--start")
        end = _parse_date(options["end"], "--end")

        try:
            report = sync_daily_candles(
                symbol=options["symbol"],
                start=start,
                end=end,
                dry_run=options["dry_run"],
            )
        except CandleFetchError as e:
            # 与「今天没有新 K 线」明确分开：这是失败，退出码非零。
            self.stderr.write(self.style.ERROR(f"日线拉取失败：{e}"))
            raise CommandError(str(e))
        except ValueError as e:
            raise CommandError(str(e))

        self.stdout.write(report.summary())
