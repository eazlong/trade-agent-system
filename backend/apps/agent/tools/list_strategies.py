"""列出所有已注册策略的名称和描述。

Agent 可用于语义匹配查找策略，而非精确名称匹配。
"""

from __future__ import annotations

import logging

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ListStrategiesTool(BaseTool):
    """
    列出所有已注册策略的名称和描述。

    用于语义匹配查找策略：将用户描述与策略列表传给 LLM，
    选出最匹配的策略，而非依赖精确名称匹配。
    """

    name = "list_strategies"
    description = (
        "列出所有已注册策略的名称和描述。"
        "用于语义匹配查找策略，将用户描述与策略列表传给 LLM 选出最匹配的策略。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, **kwargs) -> ToolResult:
        """执行查询并返回结果。"""
        try:
            from apps.strategy_engine.registry import StrategyRegistry

            # 确保策略已发现
            if StrategyRegistry._strategy_path:
                StrategyRegistry.discover()

            strategies = StrategyRegistry.list_registered_with_descriptions()

            logger.info(
                "[ListStrategiesTool] found %d strategies",
                len(strategies),
            )

            return ToolResult(
                success=True,
                data={
                    "total": len(strategies),
                    "strategies": strategies,
                },
            )
        except Exception as e:
            logger.error("[ListStrategiesTool] query failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"查询失败: {e}")
