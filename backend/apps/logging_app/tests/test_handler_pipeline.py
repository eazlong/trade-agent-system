"""日志管道的回归测试。

**这组用例钉的是一条已经造成线上故障的形态**：`SystemLogHandler.emit()` 曾经对
每条日志记录 `threading.Thread(...).start()` 去写库。实测 300 条 INFO 就留下 269 个
常驻线程，而 root logger 是 INFO 级、全库 941 处 logger 调用，信号监控每 30 秒按
monitor 数量各打一条——线程数无上限增长，DB 一慢就全部阻塞在 socket 读上，整机
「端口通、用户态进程全不响应」（txy 2026-10-19 与 11-06 两次故障同型）。

所以这里断言的**不是「日志写进去了」**（那是原本就有的能力），而是：
1. 打 N 条日志之后，线程数不随 N 增长（有界队列 + 单消费线程）；
2. 队列满时丢日志而不是阻塞调用方；
3. 批量落库确实写进了 `SystemLog`，且条数对得上。

**为什么必须用 `TransactionTestCase` 而不是 `TestCase`**：写库发生在 `emit()` 之外的
另一个线程里，而 `TestCase` 把测试包在一个未提交的事务中——那个线程用的是另一条
连接，既看不见本用例建的数据，它的写入也不在用例事务里。用 `TestCase` 时本组用例
的表现是 `written=0`（写入被挡住）或跨用例计数污染（旧线程的写入漏进下一个用例），
两者都不是在测被测行为。
"""

import logging
import threading
import time

from django.test import TransactionTestCase
from unittest.mock import patch

from apps.logging_app import handler as handler_module
from apps.logging_app.handler import SystemLogHandler, get_handler_stats
from apps.logging_app.models import SystemLog


def _make_record(msg: str, name: str = "apps.probe") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="probe.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


class _PipelineTestBase(TransactionTestCase):
    """给每个用例一个干净的管道状态，并在结束时把消费线程真的停掉。

    `_queue` / `_consumer` 是模块级单例（有意为之：一个进程只该有一个写库线程）。
    用例之间必须重置，否则上一个用例留下的线程会让「线程数没有增长」这条断言恒真
    而失去意义。
    """

    #: 让「阻塞写库」的用例能自己解开阻塞，cleanup 才不会卡住 join。
    _blocker: threading.Event | None = None

    def setUp(self):
        super().setUp()
        self._blocker = None
        self._reset_pipeline()
        SystemLog.objects.all().delete()
        self.addCleanup(self._reset_pipeline)

    def _reset_pipeline(self):
        """真的停掉旧消费线程，再清空模块状态。

        只把 ``_queue`` 置 None 不会让旧线程退出——它仍持有旧队列引用、仍会往库里
        写，于是下一个用例的 ``written`` 被它污染（「入队 120、写出 125」就是这种
        泄漏）。先解开任何人为阻塞，再投毒丸并 join。
        """
        if self._blocker is not None:
            self._blocker.set()

        q = handler_module._queue
        consumer = handler_module._consumer
        if q is not None and consumer is not None and consumer.is_alive():
            try:
                q.put_nowait(None)  # 毒丸
                consumer.join(timeout=5.0)
            except Exception:
                pass

        handler_module._queue = None
        handler_module._consumer = None
        handler_module._consumer_pid = None
        for key in handler_module._stats:
            handler_module._stats[key] = 0

    def _emit_many(self, count: int, handler: SystemLogHandler | None = None) -> None:
        h = handler or SystemLogHandler()
        for i in range(count):
            h.emit(_make_record(f"pipeline probe {i}"))

    def _wait_for_writes(self, expected: int, timeout: float = 10.0) -> int:
        """等到 written 达到预期（写库在另一个线程里，必须等它）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            stats = get_handler_stats()
            if stats["written"] >= expected:
                return stats["written"]
            time.sleep(0.05)
        return get_handler_stats()["written"]


class SystemLogHandlerBoundedThreadTest(_PipelineTestBase):
    """emit 不得为每条记录创建线程。"""

    def test_emit_does_not_spawn_a_thread_per_record(self):
        baseline = threading.active_count()
        self._emit_many(500)
        # 等到写库追上，确保不是「线程还没建出来」的假绿
        self._wait_for_writes(500)

        growth = threading.active_count() - baseline
        self.assertLessEqual(
            growth,
            1,
            f"500 条日志让线程数从 {baseline} 涨到 {threading.active_count()}；"
            "emit() 必须只入队，不得每条起一个线程",
        )

    def test_one_consumer_thread_per_process(self):
        """反复 emit 只会复用同一个消费线程（复用而非新建）。"""
        h = SystemLogHandler()
        self._emit_many(50, handler=h)
        first_pid = handler_module._consumer_pid
        first_thread = handler_module._consumer

        self._emit_many(50, handler=h)

        self.assertEqual(handler_module._consumer_pid, first_pid)
        self.assertIs(
            handler_module._consumer,
            first_thread,
            "消费线程被重建了；同一进程内应当复用同一个写库线程",
        )
        self._wait_for_writes(100)


class _AlwaysFullQueue:
    """一个永远满的队列替身：``put_nowait`` 必抛 ``queue.Full``。

    **为什么不用「构造真队列 + 卡住消费线程」这种写法**：那种写法的收尾很脆。
    让消费线程阻塞在写库上，`patch` 退出后它会用**真实**的 ``_write_db`` 把手里那
    一批写进库，于是下一用例的 ``written`` 凭空多出几条（本组用例曾因此报出
    「入队 120、写出 125」）。被测量的行为是 ``emit()`` 在队满时的反应，直接给一个
    必满的队列就能精确测到，不必牵进线程与 IO。
    """

    def put_nowait(self, item):
        import queue as _queue

        raise _queue.Full


class SystemLogHandlerBoundedQueueTest(_PipelineTestBase):
    """队列满时丢日志，不得阻塞调用方。"""

    def test_full_queue_drops_instead_of_blocking(self):
        h = SystemLogHandler()

        with patch.object(
            handler_module, "_ensure_consumer", lambda: _AlwaysFullQueue()
        ):
            start = time.monotonic()
            for i in range(200):
                h.emit(_make_record(f"flood {i}"))
            elapsed = time.monotonic() - start

            # 若 emit 在队满时阻塞（或退化成同步写库），这里必然远超 1 秒。
            self.assertLess(
                elapsed,
                1.0,
                f"200 条日志写进满队列耗时 {elapsed:.1f}s，emit() 阻塞了调用方",
            )
            stats = get_handler_stats()
            self.assertEqual(
                stats["dropped"],
                200,
                f"满队列应当全部丢弃并计数，实际 dropped={stats['dropped']}",
            )
            self.assertEqual(stats["enqueued"], 0, "满队列不该有入队成功的记录")

    def test_queue_is_bounded_by_config(self):
        """队列容量取自配置且有上界，不是一个无界 queue。"""
        q = handler_module._ensure_consumer()
        self.assertEqual(q.maxsize, handler_module._QUEUE_MAX)
        self.assertGreater(handler_module._QUEUE_MAX, 0)


class SystemLogHandlerPersistenceTest(_PipelineTestBase):
    """批量写库确实落进 SystemLog（新实现不能为了省线程而丢数据）。"""

    @patch("apps.logging_app.handler._push_stream", lambda batch: None)
    def test_batch_writes_reach_database(self):
        h = SystemLogHandler()
        self._emit_many(120, handler=h)

        written = self._wait_for_writes(120)
        self.assertEqual(
            written,
            120,
            f"入队 120 条，只写出 {written} 条",
        )
        self.assertEqual(
            SystemLog.objects.filter(message__startswith="pipeline probe").count(),
            120,
            "批量落库的条数与入队数不一致",
        )

    @patch("apps.logging_app.handler._push_stream", lambda batch: None)
    def test_module_and_level_are_preserved(self):
        h = SystemLogHandler()
        h.emit(
            logging.LogRecord(
                name="apps.trading.executor",
                level=logging.ERROR,
                pathname="probe.py",
                lineno=1,
                msg="order rejected",
                args=(),
                exc_info=None,
            )
        )
        self._wait_for_writes(1)

        row = SystemLog.objects.get(message__contains="order rejected")
        self.assertEqual(row.module, "trading")
        self.assertEqual(row.level, "ERROR")

    def test_stream_failure_does_not_block_db_write(self):
        """Redis 不可用时，DB 那条出口必须照常工作（两条出口互不牵连）。"""
        with patch(
            "apps.logging_app.handler._get_redis",
            side_effect=RuntimeError("redis down"),
        ):
            h = SystemLogHandler()
            self._emit_many(20, handler=h)
            written = self._wait_for_writes(20)
            self.assertEqual(written, 20, "Redis 失败把整批 DB 写入一起拖掉了")
            self.assertEqual(
                SystemLog.objects.filter(message__startswith="pipeline probe").count(),
                20,
            )
            self.assertGreater(
                get_handler_stats()["stream_failed"], 0, "stream 失败没有被计数"
            )
