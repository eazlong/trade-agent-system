"""MemoryFacade 的单元测试 — 全部用 InMemory* 实现，零外部依赖。

这些测试证明了方案 C 的核心价值：
- 不再需要 MockMemoryManager（60 行手写替身）
- 不再需要真实的 Redis / PostgreSQL
- 多 agent 共享语义可直接测试
"""

from __future__ import annotations

import asyncio

import pytest

from apps.memory.conversation import InMemoryConversationLog
from apps.memory.facade import MemoryFacade
from apps.memory.longterm import InMemoryLongTerm
from apps.memory.working import InMemoryWorkingMemory


@pytest.fixture
def facade() -> MemoryFacade:
    """构建一个完全内存的 facade，用于所有测试。"""
    return MemoryFacade(
        conv=InMemoryConversationLog(),
        working=InMemoryWorkingMemory(user_id="u1", agent_name="analyst"),
        longterm=InMemoryLongTerm(user_id="u1", agent_name="analyst"),
    )


# ------------------------------------------------------------------ #
#  build_context                                                       #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_build_context_dedupes_across_sources(facade):
    """working + longterm 都命中同一 content，应只返回一次。"""
    await facade.remember("BTC is bullish", persist_to="both")
    results = await facade.build_context("BTC")
    assert len(results) == 1
    assert results[0].content == "BTC is bullish"


@pytest.mark.asyncio
async def test_build_context_sorts_by_ts_desc(facade):
    """不同 ts 的条目应按 ts 降序排列。"""
    # 直接调用底层 seam，用 scope= 控制写入位置
    await facade._working.remember("old news", scope="private")
    await asyncio.sleep(0.01)
    await facade._working.remember("new news", scope="private")

    results = await facade.build_context("news")
    assert [r.content for r in results] == ["new news", "old news"]


@pytest.mark.asyncio
async def test_build_context_respects_top_k(facade):
    """返回条目数不超过 top_k。"""
    for i in range(5):
        await facade.remember(f"item {i}", persist_to="working")
    results = await facade.build_context("item", top_k=3)
    assert len(results) == 3


# ------------------------------------------------------------------ #
#  remember                                                            #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_remember_default_writes_both(facade):
    """默认 persist_to='both' 应同时写到 working + longterm。"""
    await facade.remember("shared fact")

    working = await facade._working.recall("fact")
    longterm = await facade._longterm.search("fact")
    assert any(e.content == "shared fact" for e in working)
    assert any(e.content == "shared fact" for e in longterm)


@pytest.mark.asyncio
async def test_remember_working_only(facade):
    """persist_to='working' 只写 working，longterm 保持空。"""
    await facade.remember("hot note", persist_to="working")

    working = await facade._working.recall("note")
    longterm = await facade._longterm.search("note")
    assert any(e.content == "hot note" for e in working)
    assert longterm == []


@pytest.mark.asyncio
async def test_remember_longterm_only(facade):
    """persist_to='longterm' 只写 longterm，working 保持空。"""
    await facade.remember("permanent fact", persist_to="longterm")

    working = await facade._working.recall("fact")
    longterm = await facade._longterm.search("fact")
    assert working == []
    assert any(e.content == "permanent fact" for e in longterm)


# ------------------------------------------------------------------ #
#  对话历史                                                             #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_conv_append_and_get(facade):
    entries = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]
    result = await facade.append_conv(entries)
    assert len(result) == 2
    assert (await facade.get_conv()) == entries


@pytest.mark.asyncio
async def test_conv_keep_turns(facade):
    await facade.append_conv([{"role": "user", "text": f"m{i}"} for i in range(10)])
    result = await facade.get_conv(max_turns=3)
    assert len(result) == 3
    assert result[-1]["text"] == "m9"


@pytest.mark.asyncio
async def test_conv_clear(facade):
    await facade.append_conv([{"role": "user", "text": "x"}])
    await facade.clear_conv()
    assert (await facade.get_conv()) == []


# ------------------------------------------------------------------ #
#  多 agent 共享语义                                                    #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_shared_pool_cross_agent_recall():
    """agent A 写 shared，agent B 应能 recall 到。"""
    shared_pool: dict = {}
    agent_a = InMemoryWorkingMemory("u1", "analyst", shared_pool=shared_pool)
    agent_b = InMemoryWorkingMemory("u1", "quant", shared_pool=shared_pool)

    await agent_a.remember("market signal from analyst", scope="shared")

    results = await agent_b.recall("market signal")
    assert len(results) == 1
    assert results[0].source == "working.shared"
    assert results[0].content == "market signal from analyst"


@pytest.mark.asyncio
async def test_shared_pool_excludes_own_agent():
    """recall shared 时排除自己写的（与 Redis 实现行为一致）。"""
    shared_pool: dict = {}
    agent_a = InMemoryWorkingMemory("u1", "analyst", shared_pool=shared_pool)

    await agent_a.remember("my own note", scope="shared")

    results = await agent_a.recall("own note")
    assert results == []


@pytest.mark.asyncio
async def test_private_isolated_across_agents():
    """private 写不同 agent 互不可见。"""
    shared_pool: dict = {}
    agent_a = InMemoryWorkingMemory("u1", "analyst", shared_pool=shared_pool)
    agent_b = InMemoryWorkingMemory("u1", "quant", shared_pool=shared_pool)

    await agent_a.remember("analyst private", scope="private")
    await agent_b.remember("quant private", scope="private")

    a_results = await agent_a.recall("private")
    b_results = await agent_b.recall("private")

    assert len(a_results) == 1 and a_results[0].content == "analyst private"
    assert len(b_results) == 1 and b_results[0].content == "quant private"
