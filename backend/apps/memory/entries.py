"""MemoryEntry — seam 之间的通用货币。

WorkingMemory.recall 与 LongTermMemory.search 都返回同构的 MemoryEntry，
MemoryFacade 才能跨源去重、按 ts 排序、截断 top_k。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """召回结果的标准形状。

    Attributes:
        source: 来源标识。取值 "working.private" / "working.shared" / "longterm"
        content: 记忆正文
        ts: Unix 时间戳（秒），用于跨源排序
        metadata: 任意附加字段（memory_type / importance / agent_name 等）
        score: 相关性分数（召回时填；按 ts 排序的场景可置 0）
    """

    source: str
    content: str
    ts: float
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """向后兼容：转换为旧 retrieve() 返回的 dict 形状。

        仅用于 MemoryManager shim 的 retrieve() 转发，新代码应直接使用属性访问。
        """
        return {
            "source": self.source,
            "content": self.content,
            "ts": self.ts,
            "metadata": self.metadata,
            "score": self.score,
            **{k: v for k, v in self.metadata.items()},
        }
