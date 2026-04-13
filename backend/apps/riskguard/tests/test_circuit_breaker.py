"""Tests for CircuitBreaker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import unittest


class TestCircuitBreakerL1(unittest.TestCase):
    """L1: CircuitBreaker 状态机逻辑"""

    def _make_mock_redis_client(self, get_return=None):
        """创建模拟 Redis 客户端（用于 aioredis.from_url 的返回值）"""
        mock = MagicMock()
        mock.get = AsyncMock(return_value=get_return)
        mock.setex = AsyncMock()
        mock.delete = AsyncMock()
        mock.incr = AsyncMock(return_value=1)
        mock.expire = AsyncMock()
        mock.aclose = AsyncMock()
        return mock

    def _patch_redis_from_url(self, mock_redis_client):
        """
        Mock redis.asyncio.from_url（同步函数，返回 Redis 对象）。
        patch 目标为 'redis.asyncio.from_url'（aioredis 导入位置）。
        """
        mock_from_url = MagicMock(return_value=mock_redis_client)
        return patch("redis.asyncio.from_url", mock_from_url)

    def test_is_open_returns_false_when_no_key(self):
        """熔断键不存在时 is_open 返回 False"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client(get_return=None)

        with self._patch_redis_from_url(mock_r):
            result = asyncio.run(cb.is_open())

        self.assertFalse(result)

    def test_is_open_returns_true_when_key_exists(self):
        """熔断键存在时 is_open 返回 True"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client(get_return="max drawdown exceeded")

        with self._patch_redis_from_url(mock_r):
            result = asyncio.run(cb.is_open())

        self.assertTrue(result)

    def test_trip_sets_redis_key_with_ttl(self):
        """trip() 应设置带 TTL 的 Redis 键"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client()

        with self._patch_redis_from_url(mock_r):
            asyncio.run(cb.trip("max drawdown exceeded"))

        mock_r.setex.assert_called_once()
        call_args = mock_r.setex.call_args[0]
        self.assertEqual(call_args[0], "circuit_breaker:user1")
        self.assertEqual(call_args[1], 86400)  # 24h default TTL

    def test_trip_custom_ttl(self):
        """trip() 支持自定义 TTL"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client()

        with self._patch_redis_from_url(mock_r):
            asyncio.run(cb.trip("api failure", ttl=3600))

        mock_r.setex.assert_called()
        self.assertEqual(mock_r.setex.call_args[0][1], 3600)

    def test_reset_deletes_both_keys(self):
        """reset() 应删除熔断键和失败计数键"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client()

        with self._patch_redis_from_url(mock_r):
            asyncio.run(cb.reset())

        self.assertEqual(mock_r.delete.call_count, 2)
        keys_deleted = [c[0][0] for c in mock_r.delete.call_args_list]
        self.assertIn("circuit_breaker:user1", keys_deleted)
        self.assertIn("circuit_fail_count:user1", keys_deleted)

    def test_record_failure_increments_counter(self):
        """record_failure() 应递增计数器"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client()
        mock_r.incr = AsyncMock(return_value=1)

        with self._patch_redis_from_url(mock_r):
            asyncio.run(cb.record_failure())

        mock_r.incr.assert_called_once_with("circuit_fail_count:user1")
        mock_r.expire.assert_called_once()

    def test_record_failure_trips_at_threshold(self):
        """连续失败达到阈值时触发熔断"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client()
        mock_r.incr = AsyncMock(return_value=3)  # 达到阈值

        with self._patch_redis_from_url(mock_r):
            asyncio.run(cb.record_failure())

        mock_r.setex.assert_called()

    def test_record_success_deletes_counter(self):
        """record_success() 应删除失败计数"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client()

        with self._patch_redis_from_url(mock_r):
            asyncio.run(cb.record_success())

        mock_r.delete.assert_called_once_with("circuit_fail_count:user1")

    def test_get_failure_count_returns_int(self):
        """get_failure_count() 应返回整数"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client(get_return="5")

        with self._patch_redis_from_url(mock_r):
            count = asyncio.run(cb.get_failure_count())

        self.assertEqual(count, 5)

    def test_get_failure_count_returns_zero_when_none(self):
        """get_failure_count() 在无计数时返回 0"""
        from apps.riskguard.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(user_id="user1")
        mock_r = self._make_mock_redis_client(get_return=None)

        with self._patch_redis_from_url(mock_r):
            count = asyncio.run(cb.get_failure_count())

        self.assertEqual(count, 0)
