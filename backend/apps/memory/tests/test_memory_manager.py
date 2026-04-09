"""Tests for MemoryManager."""
from __future__ import annotations

import asyncio
import unittest
from collections import OrderedDict
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from apps.memory.manager import MemoryManager, _L1_CACHE


class TestMemoryManagerInit(unittest.TestCase):
    def setUp(self):
        _L1_CACHE.clear()

    def test_creates_isolated_cache_per_agent(self):
        mem1 = MemoryManager(agent_type='analyst', user_id='u1')
        mem2 = MemoryManager(agent_type='risk', user_id='u1')
        mem1.write_l1('key', 'val1')
        mem2.write_l1('key', 'val2')
        self.assertEqual(mem1._l1['key'], 'val1')
        self.assertEqual(mem2._l1['key'], 'val2')

    def test_l1_key_format(self):
        mem = MemoryManager(agent_type='coach', user_id='user42')
        self.assertEqual(mem._l1_key, 'coach:user42')


class TestMemoryManagerWriteL2Shared(unittest.TestCase):
    """测试 L2 写入时同时写入共享区"""

    def setUp(self):
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='u1')

    def _make_mock_redis(self):
        r = AsyncMock()
        r.lpush = AsyncMock(return_value=1)
        r.ltrim = AsyncMock(return_value=True)
        r.expire = AsyncMock(return_value=True)
        r.lrange = AsyncMock(return_value=[])
        r.aclose = AsyncMock()
        return r

    def test_write_l2_also_writes_shared_key(self):
        mock_r = self._make_mock_redis()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            asyncio.run(self.mem.write_l2('shared insight'))
        # 应该有两次 lpush: 私有 + 共享
        self.assertEqual(mock_r.lpush.call_count, 2)

    def test_write_l2_shared_false_skips_shared_write(self):
        mock_r = self._make_mock_redis()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            asyncio.run(self.mem.write_l2('private only', shared=False))
        self.assertEqual(mock_r.lpush.call_count, 1)

    def test_write_l2_silent_on_error(self):
        with patch('redis.asyncio.from_url', side_effect=ConnectionError('refused')):
            asyncio.run(self.mem.write_l2('data'))


class TestMemoryManagerRetrieveL2(unittest.TestCase):
    """测试 L2 检索"""

    def setUp(self):
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='u1')

    def _make_mock_redis_with_data(self, private_data=None, shared_data=None):
        r = AsyncMock()
        r.aclose = AsyncMock()

        def lrange_side_effect(key, start, end):
            if f'mem:l2:analyst:u1' in key:
                return private_data or []
            elif 'shared' in key:
                return shared_data or []
            return []

        r.lrange = AsyncMock(side_effect=lrange_side_effect)
        r.lpush = AsyncMock(return_value=1)
        r.ltrim = AsyncMock(return_value=True)
        r.expire = AsyncMock(return_value=True)
        return r

    def test_retrieve_from_l2_private(self):
        import json
        private = [json.dumps({'content': 'BTC is bullish', 'type': 'signal', 'ts': 123, 'source_agent': 'analyst'}).encode()]
        mock_r = self._make_mock_redis_with_data(private_data=private)
        with patch('redis.asyncio.from_url', return_value=mock_r):
            results = asyncio.run(self.mem.retrieve('BTC', top_k=5))
        self.assertTrue(any(r.get('source') == 'l2' for r in results))

    def test_retrieve_from_l2_shared_excludes_own_agent(self):
        import json
        # 共享区有自己写的记忆（应被排除）
        shared = [json.dumps({'content': 'my own note', 'source_agent': 'analyst'}).encode()]
        mock_r = self._make_mock_redis_with_data(private_data=[], shared_data=shared)
        with patch('redis.asyncio.from_url', return_value=mock_r):
            results = asyncio.run(self.mem.retrieve('note', top_k=5))
        # 不应包含 l2_shared 来源
        self.assertFalse(any(r.get('source') == 'l2_shared' for r in results))

    def test_retrieve_l2_shared_includes_other_agents(self):
        import json
        shared = [json.dumps({'content': 'ETH bearish outlook', 'source_agent': 'risk_agent'}).encode()]
        mock_r = self._make_mock_redis_with_data(private_data=[], shared_data=shared)
        with patch('redis.asyncio.from_url', return_value=mock_r):
            results = asyncio.run(self.mem.retrieve('ETH', top_k=5))
        self.assertTrue(any(r.get('source') == 'l2_shared' for r in results))


class TestMemoryManagerL3Retrieve(TransactionTestCase):
    """测试 L3 检索"""

    def setUp(self):
        _L1_CACHE.clear()
        self.mem = MemoryManager(agent_type='analyst', user_id='u1')

    def test_retrieve_l3_by_content_match(self):
        from apps.agent.models import AgentMemory
        AgentMemory.objects.create(
            agent_type='analyst',
            content='BTC broke resistance at 60k',
            metadata={'symbol': 'BTC'},
        )
        mock_r = AsyncMock()
        mock_r.lrange = AsyncMock(return_value=[])
        mock_r.aclose = AsyncMock()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            results = asyncio.run(self.mem.retrieve('resistance', top_k=5))
        self.assertTrue(any(r['source'] == 'l3' and 'resistance' in r['content'] for r in results))

    def test_retrieve_l3_filters_by_agent_type(self):
        from apps.agent.models import AgentMemory
        AgentMemory.objects.create(
            agent_type='risk',
            content='Risk alert for BTC',
            metadata={},
        )
        mock_r = AsyncMock()
        mock_r.lrange = AsyncMock(return_value=[])
        mock_r.aclose = AsyncMock()
        with patch('redis.asyncio.from_url', return_value=mock_r):
            results = asyncio.run(self.mem.retrieve('Risk', top_k=5))
        # analyst 不应匹配 risk 的记忆
        self.assertFalse(any(r['source'] == 'l3' for r in results))
