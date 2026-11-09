"""`apps/core/redis_pool.py` 的回归测试。

**钉住的两条性质**：

1. **同 URL 复用同一个客户端**。旧写法每次 ``redis.from_url`` 都新建一个连接池并
   立刻占一条 socket，用完不复用也不显式关闭：redis-py 5.x 的 ``ConnectionPool``
   没有 ``__del__``，socket 要等 GC。``task_tracker`` 那几个读/写就在「每个任务里程碑
   都走一遍」的路径上，``--concurrency=4`` 之下按任务数×里程碑数累积。
2. **fork 后不继承父进程的池**。Celery prefork 子进程会继承父进程的 socket，父子
   共用同一个 fd——两边一起读写就是数据错乱。所以按 PID 判定。
"""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_pool():
    from apps.core import redis_pool

    redis_pool.reset_for_tests()
    yield
    redis_pool.reset_for_tests()


class TestReusePerUrl:
    def test_same_url_returns_same_client(self):
        from apps.core.redis_pool import get_sync_redis

        a = get_sync_redis("redis://localhost:6379/3")
        b = get_sync_redis("redis://localhost:6379/3")
        assert a is b, "同一 URL 反复取值必须复用同一个客户端"

    def test_different_db_is_a_different_client(self):
        from apps.core.redis_pool import get_sync_redis

        a = get_sync_redis("redis://localhost:6379/3")
        b = get_sync_redis("redis://localhost:6379/4")
        assert a is not b, "不同 DB 必须是不同客户端（否则会读写到别人的库）"

    def test_many_calls_do_not_grow_the_cache(self):
        """热路径反复取值不得让缓存里的客户端数量增长。"""
        from apps.core.redis_pool import get_client_stats, get_sync_redis

        for _ in range(200):
            get_sync_redis("redis://localhost:6379/3")

        stats = get_client_stats()
        assert stats["cached"] == 1, f"200 次调用后有 {stats['cached']} 个客户端"
        assert stats["stale"] == 0


class TestForkSafety:
    def test_pid_change_discards_parent_pool(self):
        """父进程建的客户端不能被子进程拿去用。"""
        from apps.core import redis_pool

        parent_client = redis_pool.get_sync_redis("redis://localhost:6379/3")
        parent_pid = os.getpid()

        # 模拟 fork：PID 变了
        with patch.object(redis_pool.os, "getpid", return_value=parent_pid + 1000):
            child_client = redis_pool.get_sync_redis("redis://localhost:6379/3")

        assert child_client is not parent_client, (
            "PID 变化后仍返回父进程的客户端；prefork 子进程会与父进程共用 socket，"
            "心跳与进度写入将互相踩踏"
        )

    def test_stale_entries_are_reported_not_hidden(self):
        from apps.core import redis_pool

        redis_pool.get_sync_redis("redis://localhost:6379/3")
        with patch.object(redis_pool.os, "getpid", return_value=os.getpid() + 1000):
            stats = redis_pool.get_client_stats()

        # 新的 PID 下，父进程那条缓存就是 stale（get_client_stats 如实报出来）
        assert stats["pid"] == os.getpid() + 1000


class TestUrlWithDb:
    def test_replaces_existing_db(self):
        from apps.core.redis_pool import url_with_db

        assert url_with_db("redis://redis:6379/0", 3) == "redis://redis:6379/3"
        assert url_with_db("redis://redis:6379", 3) == "redis://redis:6379/3"

    def test_is_idempotent(self):
        from apps.core.redis_pool import url_with_db

        once = url_with_db("redis://redis:6379/2", 3)
        twice = url_with_db(once, 3)
        assert once == twice, "反复套用不能把 DB 号追加两次"

    def test_trailing_slash_is_tolerated(self):
        from apps.core.redis_pool import url_with_db

        assert url_with_db("redis://redis:6379/", 3) == "redis://redis:6379/3"
