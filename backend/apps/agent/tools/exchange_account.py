"""Exchange account query tool for SupervisorAgent."""

from __future__ import annotations

import logging

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class GetExchangeAccountTool(BaseTool):
    """
    查询用户交易所账号基本信息。

    根据交易所类型（如 binance、okx、bybit）或标签筛选，
    返回账号的基本信息（不含 API 密钥明文）。
    """

    name = "get_exchange_account"
    description = (
        "查询用户的交易所账号基本信息。"
        '可按交易所类型（exchange 参数）筛选，如 "binance"、"okx"、"bybit"。'
        "返回结果包括账号标签、交易所名称、是否活跃、创建时间等，不包含 API 密钥。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "exchange": {
                    "type": "string",
                    "description": "可选，交易所类型筛选。支持值：binance, okx, bybit",
                },
                "label": {
                    "type": "string",
                    "description": "可选，账号标签关键词模糊匹配",
                },
            },
            "required": [],
        }

    def _execute_query(self, exchange: str, label: str) -> list:
        """Synchronous DB query."""
        from apps.exchange.models import ExchangeAccount

        qs = ExchangeAccount.objects.filter(is_active=True)
        if exchange:
            qs = qs.filter(exchange__iexact=exchange)
        if label:
            qs = qs.filter(label__icontains=label)
        return list(qs.order_by("-created_at"))

    async def execute(
        self, exchange: str = "", label: str = "", **kwargs
    ) -> ToolResult:
        try:
            from asgiref.sync import sync_to_async

            accounts = await sync_to_async(self._execute_query, thread_sensitive=True)(
                exchange, label
            )
            if not accounts:
                parts = []
                if exchange:
                    parts.append(f"交易所: {exchange}")
                if label:
                    parts.append(f"标签关键词: {label}")
                condition = "，".join(parts) if parts else "任何条件"
                return ToolResult(
                    success=True,
                    data=f"未找到符合条件的交易所账号（{condition}）。",
                )

            lines = [f"共找到 {len(accounts)} 个交易所账号："]
            for acc in accounts:
                lines.append(
                    f"- {acc.label or '未命名'} | 交易所: {acc.exchange} "
                    f"| 状态: {'活跃' if acc.is_active else '停用'} "
                    f"| 创建: {acc.created_at.strftime('%Y-%m-%d %H:%M')}"
                )

            return ToolResult(success=True, data="\n".join(lines))

        except Exception as e:
            logger.error("[GetExchangeAccountTool] error: %s", e)
            return ToolResult(success=False, error=f"查询交易所账号失败: {e}")
