"""Memory seam 协议定义。

四个协议，三种用途：
- ConversationLog — user 级对话历史（append / get / clear）
- WorkingMemory   — per-(user, agent) 短期工作记忆（remember / recall）
- LongTermMemory  — per-(user, agent) 长期知识（store / search）
- Compressor      — 注入到 ConversationLog 的可选压缩依赖

协议而非抽象基类：实现不需要继承，duck typing 即可。测试可以写最简的
InMemory 版本直接注入。
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from apps.memory.entries import MemoryEntry


@runtime_checkable
class Compressor(Protocol):
    """把旧的对话轮次压缩成一段摘要文本。

    失败时由调用方决定降级策略（通常是放弃压缩、保留原历史）。
    """

    async def compress(self, old_turns: list[dict]) -> str: ...


@runtime_checkable
class ConversationLog(Protocol):
    """user 级对话历史 seam。

    与 agent 无关：对话属于 user，不属于某个 agent。
    实现负责压缩、持久化、并发安全（append 的读-改-写）。
    """

    async def append(
        self, entries: list[dict], *, keep_turns: int | None = None
    ) -> list[dict]:
        """原子追加条目，返回追加后的完整历史。"""
        ...

    async def get(self, *, max_turns: int | None = None) -> list[dict]:
        """读取对话历史，可选按最近 N 条截断。"""
        ...

    async def clear(self) -> None:
        """清空对话历史。"""
        ...


@runtime_checkable
class WorkingMemory(Protocol):
    """per-(user, agent) 短期工作记忆 seam。

    scope 三态：
    - "private": 只写到该 agent 的私有区
    - "shared":  只写到 user 的共享区（跨 agent 可见）
    - "both":    同时写私有 + 共享（默认，与旧 write_l2(shared=True) 一致）
    """

    async def remember(
        self,
        content: str,
        *,
        scope: Literal["private", "shared", "both"] = "both",
        memory_type: str = "general",
        importance: int = 1,
    ) -> None: ...

    async def recall(self, query: str, top_k: int = 5) -> list[MemoryEntry]: ...


@runtime_checkable
class LongTermMemory(Protocol):
    """per-(user, agent) 长期知识 seam。

    写入时 metadata 由实现合并 memory_type / importance（与旧 write_l3 一致）。
    """

    async def store(
        self,
        content: str,
        *,
        metadata: dict | None = None,
        memory_type: str = "general",
        importance: int = 1,
    ) -> None: ...

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]: ...
