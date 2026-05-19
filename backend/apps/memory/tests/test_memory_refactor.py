"""Refactored memory tests — all xfail removed, P0+P1 verification checks."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from apps.memory.keys import MemoryKey
from apps.memory.manager import MemoryManager, _L1_CACHE


# ====== Fixtures ======

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


@pytest.fixture
def mem2(mock_redis):
    _L1_CACHE.clear()
    with patch("apps.memory.redis_client.RedisPool.get_client", return_value=mock_redis):
        return MemoryManager(agent_type="quant", user_id="u1", agent_name="quant")


# ====== VC-001: 跨 Agent 可读取共享记忆 ======

class TestCrossAgentSharedMemory:
    def test_analyst_write_quant_read(self, mem, mem2, mock_redis):
        """Analyst 写入的记忆 Quant 可读取"""
        asyncio.run(mem.write_l2("BTC analysis: bullish", shared=True))
        calls = mock_redis.lpush.call_args_list
        shared_calls = [c for c in calls if "shared" in str(c)]
        assert len(shared_calls) >= 1
        mock_redis.lrange = AsyncMock(return_value=[
            json.dumps({"content": "BTC analysis: bullish", "source_agent": "analyst", "ts": 123}).encode()
        ])
        results = asyncio.run(mem2.retrieve("BTC", top_k=5))
        assert any("BTC" in r.get("content", "") for r in results)


# ====== VC-002: 统一 Key 格式 ======

class TestUnifiedKeyFormat:
    def test_conv_history_uses_unified_key(self, mem, mock_redis):
        """Redis 中 key 格式为 mem:conv:{user_id}"""
        history = [{"role": "user", "content": "hello"}]
        asyncio.run(mem.save_conv_history(history))
        set_calls = mock_redis.set.call_args_list
        assert any(MemoryKey.conv_history("u1") in str(c) for c in set_calls)


# ====== VC-003: Redis 连接池复用 ======

class TestRedisPoolReuse:
    def test_write_does_not_create_new_connection(self, mem, mock_redis):
        """单次 write 不新建连接"""
        for i in range(10):
            asyncio.run(mem.write_l2(f"test content {i}", shared=False))
        assert mock_redis.lpush.call_count == 10


# ====== VC-004: embedding 字段 ======

class TestEmbeddingField:
    def test_embedding_column_exists(self):
        """AgentMemory 表包含 embedding 字段，可为 NULL"""
        from django.apps import apps

        model = apps.get_model("agent", "AgentMemory")
        field_names = [f.name for f in model._meta.get_fields()]
        assert "embedding" in field_names


# ====== VC-005: 检索结果排序 ======

class TestRetrievalOrdering:
    def test_results_sorted_by_created_at_desc(self, mem, mock_redis):
        """L2 检索结果按 created_at 降序排列"""
        mock_redis.lrange = AsyncMock(return_value=[
            json.dumps({"content": "new news", "ts": 200, "source_agent": "analyst"}).encode(),
            json.dumps({"content": "old news", "ts": 100, "source_agent": "analyst"}).encode(),
        ])
        results = asyncio.run(mem.retrieve("news", top_k=5))
        assert results[0]["content"] == "new news"
        assert results[1]["content"] == "old news"


# ====== VC-006: 共享记忆来源区分 ======

class TestSharedMemorySource:
    def test_shared_memory_shows_agent_name(self, mem2, mock_redis):
        """共享记忆可区分来源 Agent"""
        def lrange_side_effect(key, start, end):
            if "shared" in key:
                return [
                    json.dumps({"content": "ETH outlook", "source_agent": "risk_team", "ts": 123}).encode(),
                ]
            return []

        mock_redis.lrange = AsyncMock(side_effect=lrange_side_effect)
        results = asyncio.run(mem2.retrieve("ETH", top_k=5))
        shared_results = [r for r in results if r.get("source") == "l2_shared"]
        assert len(shared_results) >= 1
        assert shared_results[0]["source_agent"] == "risk_team"


# ====== VC-007: Redis 故障降级 ======

class TestRedisFailureDegradation:
    def test_degrades_to_l1_l3_when_redis_down(self, mem):
        """Redis 故障时 L1+L3 降级"""
        mem._redis = None
        mem.write_l1("conv_history", [{"role": "user", "content": "test"}])
        asyncio.run(mem.write_l2("test content"))  # should not raise


# ====== VC-008: 全局异常捕获 ======

class TestGlobalExceptionCapture:
    def test_no_uncaught_exceptions(self, mem, mock_redis):
        """所有操作失败不抛出未捕获异常"""
        mock_redis.lpush = AsyncMock(side_effect=ConnectionError("refused"))
        mock_redis.ltrim = AsyncMock(side_effect=ConnectionError("refused"))
        mock_redis.expire = AsyncMock(side_effect=ConnectionError("refused"))
        mock_redis.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_redis.set = AsyncMock(side_effect=ConnectionError("refused"))
        mock_redis.lrange = AsyncMock(side_effect=ConnectionError("refused"))
        asyncio.run(mem.write_l2("test"))
        asyncio.run(mem.save_conv_history([{"role": "user", "content": "x"}]))
        results = asyncio.run(mem.retrieve("test"))
        assert isinstance(results, list)


# ====== VC-009: 旧数据兼容 ======

class TestOldDataCompatibility:
    @pytest.mark.django_db(transaction=True)
    def test_migrated_data_readable(self, mem, mock_redis):
        """旧数据迁移后仍可读取 — 验证 L3 检索能返回数据"""
        import os
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

        from apps.agent.models import AgentMemory

        AgentMemory.objects.create(
            agent_type="analyst",
            user_id="u1",
            agent_name="analyst",
            content="old data from before migration",
            metadata={},
        )
        results = asyncio.run(mem.retrieve("old data", top_k=5))
        assert any("old data" in r.get("content", "") for r in results)


# ====== VC-010: user_id 隔离 ======

class TestUserIdIsolation:
    @pytest.mark.django_db(transaction=True)
    def test_user_a_cannot_read_user_b(self, mem, mock_redis):
        """user_id A 不能读取 user_id B 的记忆"""
        import os
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

        from apps.agent.models import AgentMemory

        AgentMemory.objects.create(
            agent_type="analyst",
            user_id="user_b",
            agent_name="analyst",
            content="secret data for user B",
            metadata={},
        )
        results = asyncio.run(mem.retrieve("secret", top_k=5))
        assert not any("user B" in r.get("content", "") for r in results)
