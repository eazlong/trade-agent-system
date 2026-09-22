"""切片全量重算入口（第①段单元 6ii）。

CONTEXT.md 要求切片「必须有全量重算入口」。这个命令就是它：判定算法调整、日线回填、
证据门槛改了之后，用它把存量回测的切片补齐/刷新。

**「全量」读作「把全部结果过一遍」，不是「无条件全部重写」。** 默认只重算三种结果：
没有切片的、标签指纹变了的、门槛变了的（判据是 `slicing.is_stale`，三条一起看）。
原因：`computed_at` 天天在变，而「算得比现在早」不是陈旧——拿它当陈旧会让这个入口
每次都是全表重写，而它存在的意义恰恰是「只补该补的那些」。需要无条件重写时用 `--force`。

标签在本命令里**只算一次**（全市场共用一份），批量重算的全部收益就在这：逐个结果各调
一次 `load_tags()` 会把一次 `label_series` 变成一千次。

它的定位是**同步**入口：在管理机上跑完就完了，不投递任务。所以长循环里的连接纪律
（`close_old_connections()`）必须自带——见 CLAUDE.md。

幂等：反复运行不产生副作用，第二次跑会把上一次的全判成「不陈旧」并跳过。

后续（单元 7）池化表的全量重算并入这里，不另开命令（CONTEXT.md）。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import Q

from apps.backtest.models import BacktestResult
from apps.regime import slicing

#: 每多少条断一次旧连接。与 `run_grid_search_task` 的逐组合清理同一个意图。
BATCH_SIZE = 50


class Command(BaseCommand):
    help = "重算存量回测的行情阶段切片（metrics.regime_slice）；幂等，按需重算"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="全部回测结果（与 --result / --strategy 至少给一个）",
        )
        parser.add_argument(
            "--result",
            action="append",
            default=[],
            metavar="ID",
            help="指定回测结果 id，可重复",
        )
        parser.add_argument(
            "--strategy",
            action="append",
            default=[],
            metavar="NAME",
            help="指定策略名（`Strategy.name` 精确匹配），可重复",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="最多处理多少个结果，0 = 不限",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="无条件重算，不做陈旧判定",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只报会重算哪些，不写库",
        )

    def handle(self, *args, **options):
        if not (options["all"] or options["result"] or options["strategy"]):
            raise CommandError("至少给一个范围：--all / --result / --strategy")

        queryset = self._select(options)
        limit = options["limit"]
        if limit > 0:
            queryset = queryset[:limit]

        # 标签算一次，全批共用。空标签（日线还没回填）不是错误：切片会照落、四格全
        # unknown，那是可读的结论；但要吼一声，否则「全 unknown」看起来像算法坏了。
        tags = slicing.load_tags()
        if not tags:
            self.stderr.write(
                self.style.WARNING("当前没有任何已判定日线，切片将全部为 unknown")
            )

        force = options["force"]
        dry_run = options["dry_run"]
        recomputed = 0
        fresh = 0
        failed: list[str] = []

        for index, result in enumerate(queryset.iterator(chunk_size=BATCH_SIZE)):
            if index % BATCH_SIZE == 0:
                close_old_connections()

            if not force and not slicing.is_stale(result, tags):
                fresh += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] 会重算 {result.id} {self._label(result)}")
                recomputed += 1
                continue

            try:
                slicing.compute_slice(result, tags)
                recomputed += 1
            except Exception as e:
                # 单条失败不中断整批：坏的那一条不该挡住其余几百条。批末以非零退出码结束。
                failed.append(str(result.id))
                self.stderr.write(
                    self.style.ERROR(f"切片失败 {result.id} {self._label(result)}：{e}")
                )

        self.stdout.write(
            f"切片重算：{'待' if dry_run else ''}重算 {recomputed}，"
            f"跳过（不陈旧）{fresh}，失败 {len(failed)}"
        )
        if failed:
            raise CommandError(f"{len(failed)} 条切片失败，详见上面的 stderr")

    def _select(self, options):
        """按范围参数拼查询集。三个参数是**并集**，不互相收窄。"""
        queryset = BacktestResult.objects.all()
        if options["all"]:
            return queryset.order_by("created_at")

        condition = Q()
        if options["result"]:
            condition |= Q(id__in=options["result"])
        if options["strategy"]:
            condition |= Q(strategy__name__in=options["strategy"])
        return queryset.filter(condition).order_by("created_at")

    @staticmethod
    def _label(result) -> str:
        return f"{result.symbol} {result.timeframe} {result.start_date}~{result.end_date}"
