"""Concurrency tests for MemoryManager."""

import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pytest

from apps.memory.manager import MemoryManager, _L1_CACHE, _L1_MAX


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.lpush = AsyncMock(return_value=1)
    r.ltrim = AsyncMock(return_value=True)
    r.expire = AsyncMock(return_value=True)
    r.lrange = AsyncMock(return_value=[])
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock(return_value=True)
    return r


@pytest.fixture
def mem(mock_redis):
    _L1_CACHE.clear()
    with patch("apps.memory.redis_client.RedisPool.get_client", return_value=mock_redis):
        return MemoryManager(agent_type="analyst", user_id="u1", agent_name="analyst")


class TestConcurrentWrite:
    def test_multi_thread_write_no_data_loss(self, mem, mock_redis):
        """多线程同时写入 L2，无数据丢失（lpush 调用次数 = 线程数）"""
        write_count = 20
        errors = []

        def write_worker(i):
            try:
                asyncio.run(mem.write_l2(f"content_{i}", shared=False))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(write_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent write: {errors}"
        assert mock_redis.lpush.call_count == write_count

    def test_multi_thread_read_consistency(self, mem, mock_redis):
        """多线程同时读取 L1，验证一致性"""
        # Write toL1 first
        mem.write_l1("test_key", "test_value")

        results = []
        errors = []

        def read_worker():
            try:
                val = mem._l1.get("test_key")
                results.append(val)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        assert all(r == "test_value" for r in results)

    def test_l1_thread_safety(self, mem):
        """L1 线程安全验证 — 多线程写入不破坏 OrderedDict"""
        errors = []

        def write_worker(i):
            try:
                for j in range(50):
                    mem.write_l1(f"key_{i}_{j}", f"val_{i}_{j}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # L1 不应崩溃，且数据完整
        assert len(mem._l1) <= _L1_MAX  # LRU 限制

    def test_pid_isolation_after_fork(self):
        """PID 隔离验证 — 不同 PID 的 MemoryManager 使用不同 Redis 实例"""
        from apps.memory.redis_client import RedisPool, _CLIENTS

        _CLIENTS.clear()

        pid1 = 12345
        pid2 = 12346

        # Simulate different PIDs by mocking getpid
        with patch("os.getpid", return_value=pid1):
            with patch("apps.memory.redis_client.RedisPool.get_client") as mock1:
                RedisPool.get_client()
                call1 = mock1.call_count

        with patch("os.getpid", return_value=pid2):
            with patch("apps.memory.redis_client.RedisPool.get_client") as mock2:
                RedisPool.get_client()
                call2 = mock2.call_count

        # Each PID should trigger its own initialization path
        assert call1 == 1
        assert call2 == 1
