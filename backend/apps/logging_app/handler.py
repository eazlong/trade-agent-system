"""
Custom logging handler that writes logs to:
1. Django DB (SystemLog model) — async, non-blocking
2. Redis Stream — for real-time SSE streaming

**为什么不是一个日志一个线程**：旧实现每条记录都 ``threading.Thread(...)`` 起一个
线程去写库。实测 300 条 INFO 日志留下 269 个常驻线程（2 类进程都成立：``django.setup``
会把 ``SystemLogHandler`` 装回 root，即便 Celery 先执行了 hijack）。root logger 是
INFO 级、全库 941 处 ``logger.info/warning/error``，而信号监控每 30 秒就按 monitor
数量各打一条——线程数因此无上限增长，DB 一慢就全部阻塞在 socket 读上（线程栈默认
8MB 虚拟），最终表现为「端口通、用户态进程整体不响应」（txy 2026-10-19 / 11-06 的
两种故障形态即此）。

本实现把「日志量」与「线程数」解耦：``emit()`` 只做格式化 + 有界入队（``put_nowait``，
满即丢并计数，绝不阻塞调用方），由一个**单**消费线程批量取走、批量 ``bulk_create``、
Redis 走 pipeline 一次往返。线程数恒定，RSS 不再随日志量线性增长。

**fork 安全**：Celery `--concurrency=4` 是 prefork，父进程里已启动的消费线程不会跟着
子进程跑（且 threading 锁可能恰好被 fork 带走）。所以按 PID 判定，PID 变了就换一个
新的队列与线程——与 ``apps/memory/redis_client.py`` 的「按 PID 缓存客户端」同一条纪律。
父进程队列里尚未落库的记录不复制给子进程（那会造成重复写入），子进程从空队列开始。
"""

import atexit
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone

import redis

from .mapper import resolve_module

_redis_client = None
_redis_lock = threading.Lock()


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is None:
            url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            _redis_client = redis.Redis.from_url(url, decode_responses=True)
        return _redis_client


# Stream key for real-time log broadcasting
LOG_STREAM_KEY = "system:logs"
# Max stream length (keep last 10000 entries)
LOG_STREAM_MAXLEN = 10000

# ── 队列与批量参数 ──────────────────────────────────────────────────────────
# 队列上限：超过即丢（并进 ``dropped`` 计数），绝不阻塞 emit。10000 条足够吸收
# 一次日志风暴，又不至于自己成为内存问题——这正是旧实现缺的那道上界。
_QUEUE_MAX = int(os.environ.get("LOG_DB_QUEUE_MAX", "10000"))
# 单批上限：一条 INSERT 写多少行。太大则单次写库耗时长，太小则往返多。
_BATCH_SIZE = int(os.environ.get("LOG_DB_BATCH_SIZE", "200"))
# 攒批等待：日志稀疏时最多等这么久就把手里的写掉，避免「最后一条迟迟不落库」。
_FLUSH_INTERVAL = float(os.environ.get("LOG_DB_FLUSH_INTERVAL", "0.5"))

# 统计口径：只用来自查「有没有在丢日志」，不写日志（handler 里写日志会自我递归）。
_stats = {"enqueued": 0, "written": 0, "dropped": 0, "stream_failed": 0}

_state_lock = threading.Lock()
_queue: "queue.Queue | None" = None
_consumer: "threading.Thread | None" = None
_consumer_pid: "int | None" = None


def get_handler_stats() -> dict:
    """返回本进程的日志管道计数（排障用：``dropped`` 持续增长即队列过载）。"""
    return dict(_stats)


def _ensure_consumer() -> "queue.Queue":
    """确保本进程有一个队列 + 一个在跑的消费线程，返回该队列。

    PID 变化（Celery prefork 后的子进程）时整体重建：新队列、新线程。旧队列里
    没落库的记录不继承，宁可丢也不重复写——重复写会让 ``system_logs`` 出现
    子进程数倍的重复行。
    """
    global _queue, _consumer, _consumer_pid

    pid = os.getpid()
    q = _queue
    if q is not None and _consumer_pid == pid and _consumer is not None:
        if _consumer.is_alive():
            return q

    with _state_lock:
        if (
            _queue is not None
            and _consumer_pid == os.getpid()
            and _consumer is not None
            and _consumer.is_alive()
        ):
            return _queue

        _queue = queue.Queue(maxsize=_QUEUE_MAX)
        _consumer_pid = os.getpid()
        _consumer = threading.Thread(
            target=_consumer_loop,
            args=(_queue,),
            name="system-log-writer",
            daemon=True,
        )
        _consumer.start()
        return _queue


def _consumer_loop(q: "queue.Queue") -> None:
    """单消费线程：批量取、批量写。进程退出时由 ``atexit`` 投毒丸收尾。"""
    while True:
        first = q.get()
        if first is None:  # 毒丸：把剩余的都写完再退出
            q.task_done()
            break

        batch = [first]
        deadline = time.monotonic() + _FLUSH_INTERVAL
        while len(batch) < _BATCH_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = q.get(timeout=remaining)
            except queue.Empty:
                break
            if item is None:  # 毒丸插在批中间：本批写完再退出
                _process_batch(batch)
                for _ in batch:
                    q.task_done()
                q.task_done()
                return
            batch.append(item)

        _process_batch(batch)
        for _ in batch:
            q.task_done()


def _process_batch(batch: list) -> None:
    """一批 = [(redis_entry, db_row_or_None), ...]。两条出口互不影响。"""
    try:
        _push_stream(batch)
    finally:
        _write_db(batch)


def _push_stream(batch: list) -> None:
    """Redis Stream 用 pipeline 一次往返，而不是每条一次 xadd。"""
    try:
        r = _get_redis()
        pipe = r.pipeline()
        for entry, _ in batch:
            pipe.xadd(LOG_STREAM_KEY, entry, maxlen=LOG_STREAM_MAXLEN, approximate=True)
        pipe.execute()
    except Exception:
        _stats["stream_failed"] += len(batch)


def _write_db(batch: list) -> None:
    rows = [row for _, row in batch if row is not None]
    if not rows:
        return
    try:
        # 消费线程是长驻线程：每批写前刷新连接，避免 pgbouncer/DB idle timeout
        # 把连接掐掉后整批静默失败（与 apps/backtest/tasks.py 同一写法）。
        from django.db import close_old_connections

        close_old_connections()

        from apps.logging_app.models import SystemLog

        SystemLog.objects.bulk_create(
            [SystemLog(**row) for row in rows],
            batch_size=_BATCH_SIZE,
        )
        _stats["written"] += len(rows)
    except Exception:
        pass  # DB write failure should not surface


def _flush_at_exit() -> None:
    """进程退出前把队列里剩下的写完（最多等 2 秒）。"""
    q = _queue
    consumer = _consumer
    if q is None or consumer is None or _consumer_pid != os.getpid():
        return
    try:
        q.put_nowait(None)
        consumer.join(timeout=2.0)
    except Exception:
        pass


atexit.register(_flush_at_exit)


class SystemLogHandler(logging.Handler):
    """
    Logging handler that captures all Python logging output and stores it
    in the SystemLog DB model + pushes to Redis Stream for real-time streaming.

    ``emit()`` 只做格式化与入队，真正的 IO 在单消费线程里批量做。
    """

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            # Truncate early to prevent large messages (e.g. full conversation
            # history dumps) from bloating Redis streams and the DB.
            if len(msg) > 4000:
                msg = msg[:4000] + "...[truncated]"
            module = resolve_module(record.name)
            level_name = record.levelname
            trace_id = getattr(record, "trace_id", "")
            extra_data = {}
            # Collect extra fields from the log record
            for key in (
                "request_id",
                "user_id",
                "task_id",
                "duration_ms",
                "token_used",
            ):
                val = getattr(record, key, None)
                if val is not None:
                    extra_data[key] = val

            entry = {
                "level": level_name,
                "module": module,
                "logger": record.name,
                "message": msg,
                "trace_id": trace_id,
                "extra": json.dumps(extra_data),
                "ts": datetime.now(timezone.utc).isoformat(),
            }

            db_row = None
            if not getattr(record, "_suppress_db", False):
                db_row = {
                    "level": level_name,
                    "module": module,
                    "logger_name": record.name,
                    "message": msg[:4000],  # Cap message length
                    "trace_id": trace_id,
                    "extra_data": extra_data,
                }

            q = _ensure_consumer()
            try:
                q.put_nowait((entry, db_row))
                _stats["enqueued"] += 1
            except queue.Full:
                # 队列满 = 写库跟不上。丢日志，但绝不阻塞调用方——阻塞会把
                # 业务线程一起拖死，那比丢几条日志糟得多。
                _stats["dropped"] += 1

        except Exception:
            # Never let logging failures break the application
            self.handleError(record)


class DBOnlyLogHandler(logging.Handler):
    """Lightweight handler that only writes to DB (no Redis).

    同步写，供测试与需要确定性落库的场合使用。
    """

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            module = resolve_module(record.name)
            SystemLog = self._get_model()
            if SystemLog is not None:
                SystemLog.objects.create(
                    level=record.levelname,
                    module=module,
                    logger_name=record.name,
                    message=msg[:4000],
                    trace_id=getattr(record, "trace_id", ""),
                    extra_data={},
                )
        except Exception:
            pass

    @staticmethod
    def _get_model():
        try:
            from apps.logging_app.models import SystemLog

            return SystemLog
        except Exception:
            return None
