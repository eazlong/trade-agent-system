"""判定链路的时区与时刻不变量（第①段单元 4）。

这个文件测的不是「某个常量等于某个值」，而是**「北京 08:00 出结论、次日 08:00 生效」
这句话在时刻上是否真的成立**。它成立靠三层：

1. 业务时区的日界与日线换线时刻是**同一个绝对时刻**（北京 08:00 == UTC 00:00）。
   少了这条，签署日 / 运行日 / 生效时刻就会错开半天，而错开的表现是切片把结论归到
   相邻的一天——完全看不出来。
2. 判定挂在那条既有的 5 分钟心跳上（`snapshot_daily_equity`），所以日界之后必然很快
   出结论，且心跳的既有职责（日度权益快照）不被新职责顶掉。
3. 心跳的节拍足够密，日界不会被跳过。

时区这条特别容易在后面被「简化」掉：把 `BUSINESS_TIMEZONE` 设成 UTC 能让所有
`to_business()` 变成空操作，代码看起来更简单，而 08:00 与换线时刻的对应关系就断了。
所以这里钉的是**偏移量与绝对时刻**，不是时区名字——换成任何 +08:00 的时区都应当通过。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone as django_timezone

from apps.common.time_utils import business_tz, business_tz_name
from apps.regime.candles import latest_complete_date
from apps.regime.models import business_midnight

#: 判定机制的名义运行时刻：北京 2026-09-22 08:00 == UTC 2026-09-22 00:00。
RUN_DAY = date(2026, 9, 22)


class TestBusinessDayBoundaryIsTheCandleBoundary(TestCase):
    """业务日界（08:00 北京）必须落在日线换线的那一刻上。"""

    def test_business_midnight_is_exactly_utc_midnight(self):
        for day in (date(2026, 1, 1), date(2026, 9, 22), date(2027, 3, 1)):
            with self.subTest(day=day):
                moment = business_midnight(day)
                self.assertEqual(
                    moment,
                    datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                    "业务日界必须等于该自然日的 UTC 00:00（日线换线时刻），"
                    "否则签署日/运行日/生效时刻会整体错开",
                )

    def test_business_timezone_is_utc_plus_eight(self):
        """偏移量是 +08:00 才成立；名字无所谓。

        断言偏移而不是断言时区名：任何 +08:00 的时区在行为上等价，而**任何**别的偏移
        都会让上面那条相等关系失败——中国自 1991 年起无夏令时，+08:00 是常量。
        """
        midwinter = business_midnight(date(2026, 1, 15))
        midsummer = business_midnight(date(2026, 7, 15))
        self.assertEqual(midwinter.utcoffset(), timedelta(hours=8))
        self.assertEqual(
            midsummer.utcoffset(),
            timedelta(hours=8),
            "业务时区的偏移不得随季节变化——判定记录里的 effective_at 是永久留存的事实，"
            "夏令时会让同一句「次日 08:00」在不同季节指两个不同的绝对时刻",
        )

    def test_business_timezone_is_not_the_process_timezone(self):
        """业务时区与进程时区不是同一个，且 Django 不会把业务时区当成进程时区。

        Django 在加载 settings 时会把进程 `TZ` 改成 `TIME_ZONE`（见 time_utils 模块
        docstring），所以「业务时区」这件事只能靠显式构造，不能靠进程环境。这条测试
        第一次失败就意味着有人把两者合并了——那时 `business_midnight()` 的显式时区
        构造还在，但已经没人需要它，下一步就会被删掉。
        """
        self.assertNotEqual(
            business_tz_name(),
            settings.TIME_ZONE,
            "业务时区若与 TIME_ZONE 相同，判定链路的显式时区构造就成了死代码，"
            "下一步必然被删；而 TIME_ZONE 改成非 UTC 时结论就会整体平移",
        )
        self.assertNotEqual(str(django_timezone.get_current_timezone()), business_tz_name())

    def test_business_timezone_is_a_real_iana_zone(self):
        """必须是可解析的 IANA 时区：将来真要挂 crontab 时，它要能直接写进
        `CrontabSchedule.timezone`（TimeZoneField 只收 IANA 名）。"""
        self.assertEqual(business_tz().key, business_tz_name())
        self.assertTrue(business_tz().key and "/" in business_tz().key)


class TestSignedCandleIsTheLastClosedOne(TestCase):
    """在日界那一刻运行，签署的必须是刚收盘的那一根（D−1），生效时刻是 D+1。"""

    def test_running_at_the_boundary_signs_yesterday(self):
        boundary = business_midnight(RUN_DAY)
        self.assertEqual(latest_complete_date(boundary), RUN_DAY - timedelta(days=1))

    def test_effective_moment_skips_exactly_one_day(self):
        """运行日 D → 签署日 D−1 → 生效时刻 D+1 北京 08:00。

        「今天出的结论明天才咬人」在这里就是 `effective_at - business_midnight(D) == 1 天`：
        整整一天的可见期，异常能在生效前被拦下。
        """
        attribute_date = RUN_DAY - timedelta(days=1)
        effective_at = business_midnight(attribute_date + timedelta(days=2))
        self.assertEqual(effective_at, business_midnight(RUN_DAY + timedelta(days=1)))
        self.assertEqual(effective_at - business_midnight(RUN_DAY), timedelta(days=1))

    def test_the_mapping_survives_a_late_tick(self):
        """心跳晚几分钟不影响签署日与生效时刻——它们只是运行日的函数。

        这是「名义运行日」而不是「实际执行时刻」的意义：同一天的记录不会因为
        「这是第几个 tick 跑成的」而不同。
        """
        on_time = business_midnight(RUN_DAY)
        late = on_time + timedelta(minutes=7)
        self.assertEqual(latest_complete_date(on_time), latest_complete_date(late))
        self.assertEqual(latest_complete_date(late), RUN_DAY - timedelta(days=1))


class TestJudgementRidesTheSnapshotHeartbeat(TestCase):
    """判定挂在既有的 5 分钟心跳上（对 CONTEXT.md 字面要求的一处有意偏离）。"""

    def _run_task(self):
        """跑一次 `snapshot_daily_equity` 的任务体，返回按序记录的事件名。

        两个被调用方都是**函数内 import**（本模块的既有风格：任务模块不在 import
        期就把交易/判定链路拉起来），所以替身要打在**源模块**上，而不是 ``tasks``
        模块的属性上——``tasks`` 上根本没有这两个名字。
        """
        from apps.regime import judgement
        from apps.trading import daily_snapshot, tasks

        events: list[str] = []
        snapshot = MagicMock()
        snapshot.as_dict.return_value = {}

        async def fake_snapshot():
            events.append("snapshot")
            return snapshot

        def fake_judgement(*args, **kwargs):
            events.append("judgement")
            return {"skipped": "no_candles"}

        with (
            patch.object(daily_snapshot, "write_daily_snapshots", new=fake_snapshot),
            patch.object(judgement, "run_daily_judgement", new=fake_judgement),
        ):
            tasks.snapshot_daily_equity.run()
        return events

    def test_the_heartbeat_runs_the_daily_judgement(self):
        self.assertIn("judgement", self._run_task())

    def test_the_existing_snapshot_duty_runs_first(self):
        """既有职责先跑，新职责后跑。

        判定失败会往上抛（真故障必须被任务健康检查看见），若它排在快照之前就会连带
        吞掉这一次快照。快照本来就是 5 分钟一轮的幂等写入，晚一轮无所谓，但顺序反过来
        等于让新机制有能力打断一条已在生产上运行的链路——没有理由付这个代价。
        """
        self.assertEqual(self._run_task(), ["snapshot", "judgement"])

    def test_a_failing_judgement_still_stops_the_task(self):
        """判定抛异常时任务必须失败（而不是被吞掉）。

        失败是**期望**行为：心跳 5 分钟后再来一次，判定写入是幂等的 `get_or_create`，
        属 CONTEXT.md「读安全」那一类，可以无脑重试；而吞掉异常等于判定静默死掉。
        """
        from apps.regime import judgement
        from apps.trading import daily_snapshot, tasks

        snapshot = MagicMock()
        snapshot.as_dict.return_value = {}
        with (
            patch.object(
                daily_snapshot,
                "write_daily_snapshots",
                new=AsyncMock(return_value=snapshot),
            ),
            patch.object(judgement, "run_daily_judgement", side_effect=RuntimeError("boom")),
        ):
            with self.assertRaises(RuntimeError):
                tasks.snapshot_daily_equity.run()


class TestTheBeatIsDenseEnoughForTheDayBoundary(TestCase):
    """心跳节拍决定「日界之后多久出结论」。"""

    def test_snapshot_interval_bounds_the_delay(self):
        from celery_app import app

        entry = app.conf.beat_schedule["snapshot-daily-equity"]
        interval = entry["schedule"]
        self.assertLessEqual(
            interval,
            timedelta(hours=1).total_seconds(),
            "判定搭在这条心跳上，所以心跳间隔就是「日界之后最晚多久出结论」。"
            "放宽到小时级会让结论落在当天的后半段，与「日频、每天出结论」的语义不符",
        )
