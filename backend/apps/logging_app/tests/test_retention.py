"""日志保留策略的回归测试。

**钉住的性质**（按重要性排序）：

1. **ERROR 不会因为 INFO 的短窗口被误删**。这是分级保留的全部意义——排查线上故障
   时要翻的是 ERROR，把它的保留期和 DEBUG 拉平，等于让「回溯一个月前的同类错误」
   变成不可能。旧实现没有任何清理，所以这条性质此前**不存在**；它现在是
   存在的，必须有东西钉着它。
2. **时间边界准确**：窗口内的一行都不能被删。
3. **分批执行**：一次调用的删除量受 `max_batches × batch_size` 约束，不会把整表
   一次性塞进一个事务（小机器上那会把 DB 拖到无响应）。
4. **幂等**：重复跑不会删到保留期内的行。

背景（txy 2026-11-06 实测）：`system_logs` 3,157,406 行 / 1210 MB，其中
2026-09 单月 1,914,415 行，全部是 DEBUG/INFO 级的数据源逐条日志。
"""

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from apps.logging_app.models import SystemLog
from apps.logging_app.tasks import RETENTION_DAYS, purge_expired_logs


def _make_log(level: str, age_days: float, message: str = "x") -> SystemLog:
    """建一行「age_days 天前」的日志。

    `created_at` 是 `auto_now_add`，只能建完再 update 回去。
    """
    row = SystemLog.objects.create(level=level, module="unknown", message=message)
    created = timezone.now() - dt.timedelta(days=age_days)
    SystemLog.objects.filter(id=row.id).update(created_at=created)
    return row


class TestGradedRetention(TestCase):
    """分级保留：每个等级按自己的窗口清，互不牵连。"""

    def test_error_older_than_info_window_survives(self):
        """**最关键的一条**：过了 INFO 窗口但仍在 ERROR 窗口内的 ERROR 必须留着。"""
        _make_log("ERROR", age_days=45, message="keep-me-error")
        _make_log("INFO", age_days=45, message="should-be-gone-info")

        purge_expired_logs(max_batches=10, batch_size=100)

        self.assertTrue(
            SystemLog.objects.filter(message="keep-me-error").exists(),
            "45 天前的 ERROR 被删了；ERROR 的保留窗口（90 天）必须独立于 INFO（14 天）",
        )
        self.assertFalse(
            SystemLog.objects.filter(message="should-be-gone-info").exists(),
            "45 天前的 INFO 应当被删（INFO 窗口 14 天）",
        )

    def test_error_beyond_its_own_window_is_deleted(self):
        _make_log("ERROR", age_days=RETENTION_DAYS["ERROR"] + 5, message="old-error")
        purge_expired_logs(max_batches=10, batch_size=100)
        self.assertFalse(SystemLog.objects.filter(message="old-error").exists())

    def test_warning_window_is_its_own(self):
        _make_log("WARNING", age_days=45, message="w45")
        _make_log("WARNING", age_days=20, message="w20")
        purge_expired_logs(max_batches=10, batch_size=100)
        self.assertFalse(SystemLog.objects.filter(message="w45").exists())
        self.assertTrue(SystemLog.objects.filter(message="w20").exists())

    def test_debug_and_info_share_the_short_window(self):
        for level in ("DEBUG", "INFO"):
            _make_log(level, age_days=RETENTION_DAYS[level] + 1, message=f"old-{level}")
            _make_log(level, age_days=1, message=f"new-{level}")
        purge_expired_logs(max_batches=10, batch_size=100)
        self.assertEqual(
            SystemLog.objects.filter(message__startswith="old-").count(), 0
        )
        self.assertEqual(
            SystemLog.objects.filter(message__startswith="new-").count(), 2
        )


class TestBoundary(TestCase):
    """窗口内的行一行都不能动。"""

    def test_rows_inside_window_are_untouched(self):
        for age in (0, 1, 5, 13.5):
            _make_log("INFO", age_days=age, message=f"inside-{age}")
        purge_expired_logs(max_batches=10, batch_size=100)
        self.assertEqual(
            SystemLog.objects.filter(message__startswith="inside-").count(),
            4,
            "保留窗口内的日志被误删了",
        )

    def test_nothing_to_delete_is_not_an_error(self):
        _make_log("INFO", age_days=0, message="fresh")
        result = purge_expired_logs(max_batches=10, batch_size=100)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["deleted"], {})


class TestBatching(TestCase):
    """分批执行：单轮删除量有上界。"""

    def test_single_call_is_capped(self):
        for i in range(40):
            _make_log("INFO", age_days=30, message=f"bulk-{i}")

        result = purge_expired_logs(max_batches=2, batch_size=10)

        # 2 批 × 10 行 = 最多 20 行；剩下的留给下一轮
        self.assertEqual(
            result["total"],
            20,
            f"单轮删了 {result['total']} 行，突破了 max_batches×batch_size 上界",
        )
        self.assertEqual(
            SystemLog.objects.filter(message__startswith="bulk-").count(), 20
        )

    def test_repeated_calls_eventually_clear_everything(self):
        for i in range(25):
            _make_log("DEBUG", age_days=30, message=f"drain-{i}")

        for _ in range(5):
            purge_expired_logs(max_batches=2, batch_size=10)

        self.assertEqual(
            SystemLog.objects.filter(message__startswith="drain-").count(), 0
        )

    def test_result_reports_per_level_counts(self):
        for i in range(5):
            _make_log("INFO", age_days=30, message=f"i{i}")
        for i in range(3):
            _make_log("WARNING", age_days=60, message=f"w{i}")

        result = purge_expired_logs(max_batches=10, batch_size=100)

        self.assertEqual(result["deleted"].get("INFO"), 5)
        self.assertEqual(result["deleted"].get("WARNING"), 3)
        self.assertEqual(result["total"], 8)


class TestIdempotent(TestCase):
    """重复跑不会删到保留期内的行。"""

    def test_second_run_deletes_nothing_new(self):
        _make_log("INFO", age_days=30, message="old")
        _make_log("INFO", age_days=0, message="fresh")

        first = purge_expired_logs(max_batches=10, batch_size=100)
        second = purge_expired_logs(max_batches=10, batch_size=100)

        self.assertEqual(first["total"], 1)
        self.assertEqual(second["total"], 0)
        self.assertTrue(SystemLog.objects.filter(message="fresh").exists())

    def test_retention_map_covers_every_level_the_model_defines(self):
        """模型里新增一个 level 时，这张保留表必须跟着补——否则新等级永不被清理。"""
        model_levels = {choice[0] for choice in SystemLog.LEVEL_CHOICES}
        missing = model_levels - set(RETENTION_DAYS)
        self.assertEqual(
            missing,
            set(),
            f"这些等级没有保留窗口，会被无限期堆积：{sorted(missing)}",
        )
