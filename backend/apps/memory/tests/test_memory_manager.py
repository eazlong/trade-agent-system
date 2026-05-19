"""Tests for MemoryManager (updated for new architecture)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from apps.memory.manager import MemoryManager, _L1_CACHE


class TestMemoryManagerInit(unittest.TestCase):
    def setUp(self):
        _L1_CACHE.clear()

    def test_creates_isolated_cache_per_user(self):
        mem1 = MemoryManager(agent_type="analyst", user_id="u1")
        mem2 = MemoryManager(agent_type="analyst", user_id="u2")
        mem1.write_l1("key", "val1")
        mem2.write_l1("key", "val2")
        self.assertEqual(mem1._l1["key"], "val1")
        self.assertEqual(mem2._l1["key"], "val2")

    def test_same_user_shares_l1_key(self):
        """同 user_id 的不同 agent 共享 L1 key（统一为 conv_history）"""
        mem1 = MemoryManager(agent_type="analyst", user_id="u1")
        mem2 = MemoryManager(agent_type="risk", user_id="u1")
        self.assertEqual(mem1._l1_key, mem2._l1_key)  # 都是 mem:conv:u1

    def test_l1_key_format(self):
        mem = MemoryManager(agent_type="coach", user_id="user42")
        self.assertEqual(mem._l1_key, "mem:conv:user42")


class TestMemoryManagerWriteL2Shared(unittest.TestCase):
    """测试 L2 写入时同时写入共享区"""

    def setUp(self):
        _L1_CACHE.clear()
        mock_r = AsyncMock()
        mock_r.lpush = AsyncMock(return_value=1)
        mock_r.ltrim = AsyncMock(return_value=True)
        mock_r.expire = AsyncMock(return_value=True)
        mock_r.lrange = AsyncMock(return_value=[])
        self._redis_patch = patch(
            "apps.memory.redis_client.RedisPool.get_client", return_value=mock_r
        )
        self._redis_patch.start()
        self.mem = MemoryManager(agent_type="analyst", user_id="u1")

    def tearDown(self):
        self._redis_patch.stop()

    def test_write_l2_also_writes_shared_key(self):
        asyncio.run(self.mem.write_l2("shared insight"))
        # 通过 _redis 属性获取 mock
        mock_r = self.mem._redis
        # 应该有两次 lpush: 私有 + 共享
        self.assertEqual(mock_r.lpush.call_count, 2)

    def test_write_l2_shared_false_skips_shared_write(self):
        asyncio.run(self.mem.write_l2("private only", shared=False))
        mock_r = self.mem._redis
        self.assertEqual(mock_r.lpush.call_count, 1)

    def test_write_l2_silent_on_error(self):
        """L2 write 失败应静默处理，不抛异常"""
        mock_r = self.mem._redis
        mock_r.lpush.side_effect = ConnectionError("refused")
        # 不应抛出异常
        asyncio.run(self.mem.write_l2("data"))


class TestMemoryManagerRetrieveL2(unittest.TestCase):
    """测试 L2 检索"""

    def setUp(self):
        _L1_CACHE.clear()
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value=None)  # 用于 conv_history 降级
        mock_r.set = AsyncMock(return_value=True)
        mock_r.lpush = AsyncMock(return_value=1)
        mock_r.ltrim = AsyncMock(return_value=True)
        mock_r.expire = AsyncMock(return_value=True)

        def lrange_side_effect(key, start, end):
            if "analyst" in key and "u1" in key:
                return getattr(self, "_private_data", [])
            elif "shared" in key:
                return getattr(self, "_shared_data", [])
            return []

        mock_r.lrange = AsyncMock(side_effect=lrange_side_effect)
        self._redis_patch = patch(
            "apps.memory.redis_client.RedisPool.get_client", return_value=mock_r
        )
        self._redis_patch.start()
        self.mem = MemoryManager(agent_type="analyst", user_id="u1")

    def tearDown(self):
        self._redis_patch.stop()

    def test_retrieve_from_l2_private(self):
        import json

        private = [
            json.dumps(
                {
                    "content": "BTC is bullish",
                    "type": "signal",
                    "ts": 123,
                    "source_agent": "analyst",
                }
            ).encode()
        ]
        self._private_data = private
        results = asyncio.run(self.mem.retrieve("BTC", top_k=5))
        self.assertTrue(any(r.get("source") == "l2" for r in results))

    def test_retrieve_from_l2_shared_excludes_own_agent(self):
        import json

        # 共享区有自己写的记忆（应被排除）
        shared = [
            json.dumps({"content": "my own note", "source_agent": "analyst"}).encode()
        ]
        self._private_data = []
        self._shared_data = shared
        results = asyncio.run(self.mem.retrieve("note", top_k=5))
        # 不应包含 l2_shared 来源
        self.assertFalse(any(r.get("source") == "l2_shared" for r in results))

    def test_retrieve_l2_shared_includes_other_agents(self):
        import json

        shared = [
            json.dumps(
                {"content": "ETH bearish outlook", "source_agent": "risk_agent"}
            ).encode()
        ]
        self._private_data = []
        self._shared_data = shared
        results = asyncio.run(self.mem.retrieve("ETH", top_k=5))
        self.assertTrue(any(r.get("source") == "l2_shared" for r in results))


class TestMemoryManagerL3Retrieve(TransactionTestCase):
    """测试 L3 检索"""

    def setUp(self):
        _L1_CACHE.clear()
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value=None)
        mock_r.set = AsyncMock(return_value=True)
        mock_r.lrange = AsyncMock(return_value=[])
        mock_r.lpush = AsyncMock(return_value=1)
        mock_r.ltrim = AsyncMock(return_value=True)
        mock_r.expire = AsyncMock(return_value=True)
        self._redis_patch = patch(
            "apps.memory.redis_client.RedisPool.get_client", return_value=mock_r
        )
        self._redis_patch.start()
        self.mem = MemoryManager(agent_type="analyst", user_id="u1")

    def tearDown(self):
        self._redis_patch.stop()

    def test_retrieve_l3_by_content_match(self):
        from apps.agent.models import AgentMemory

        AgentMemory.objects.create(
            agent_type="analyst",
            agent_name="analyst",
            user_id="u1",
            content="BTC broke resistance at 60k",
            metadata={"symbol": "BTC"},
        )
        results = asyncio.run(self.mem.retrieve("resistance", top_k=5))
        self.assertTrue(
            any(r["source"] == "l3" and "resistance" in r["content"] for r in results)
        )

    def test_retrieve_l3_filters_by_user_id(self):
        """L3 现在按 user_id 过滤，不同用户的记忆应隔离"""
        from apps.agent.models import AgentMemory

        # 创建另一个用户的记忆
        AgentMemory.objects.create(
            agent_type="analyst",
            agent_name="analyst",
            user_id="other_user",
            content="Risk alert for BTC",
            metadata={},
        )
        results = asyncio.run(self.mem.retrieve("Risk", top_k=5))
        # analyst(u1) 不应匹配 other_user 的记忆
        self.assertFalse(any(r["source"] == "l3" for r in results))

    def test_retrieve_l3_includes_same_user_different_agent(self):
        """同一 user_id 下不同 agent 的记忆应被检索到"""
        from apps.agent.models import AgentMemory

        AgentMemory.objects.create(
            agent_type="risk",
            agent_name="risk",
            user_id="u1",
            content="Risk alert: BTC overbought",
            metadata={},
        )
        results = asyncio.run(self.mem.retrieve("overbought", top_k=5))
        # 同 user_id 的记忆应被检索到
        self.assertTrue(any(r["source"] == "l3" for r in results))


class TestConvHistory(unittest.TestCase):
    """测试对话历史的保存和读取"""

    def setUp(self):
        _L1_CACHE.clear()
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value=None)
        mock_r.set = AsyncMock(return_value=True)
        self._redis_patch = patch(
            "apps.memory.redis_client.RedisPool.get_client", return_value=mock_r
        )
        self._redis_patch.start()
        self.mem = MemoryManager(agent_type="supervisor", user_id="u1")

    def tearDown(self):
        self._redis_patch.stop()

    def test_save_conv_history_writes_l1_and_l2(self):
        asyncio.run(self.mem.save_conv_history([{"role": "user", "content": "hello"}]))
        self.assertIsNotNone(self.mem._l1.get("conv_history"))
        self.assertEqual(self.mem._redis.set.call_count, 1)

    def test_get_conv_history_reads_l1_first(self):
        self.mem.write_l1("conv_history", [{"role": "user", "content": "test"}])
        result = asyncio.run(self.mem.get_conv_history(max_turns=5))
        self.assertEqual(len(result), 1)

    def test_get_conv_history_truncates(self):
        history = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        self.mem.write_l1("conv_history", history)
        result = asyncio.run(self.mem.get_conv_history(max_turns=5))
        # max_turns * 2 = 10 条
        self.assertLessEqual(len(result), 10)
