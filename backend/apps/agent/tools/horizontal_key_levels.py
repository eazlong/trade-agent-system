"""Horizontal key levels tool for trading agents."""

from __future__ import annotations

from .base import BaseTool, ToolResult


class GetHorizontalKeyLevelsTool(BaseTool):
    """根据调用方传入的 K 线计算水平关键价位。"""

    name = "get_horizontal_key_levels"
    description = (
        "根据传入的K线数据计算水平关键价位。只做纯计算，不获取行情；"
        "传入 current_price 时会拆分 supports/resistances。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "klines": {
                    "type": "array",
                    "description": "K线列表，每项需包含 high、low、close，可包含 timestamp/open/volume",
                    "items": {"type": "object"},
                },
                "current_price": {
                    "type": "number",
                    "description": "可选，当前价格；传入后区分支撑位和压力位",
                },
                "atr_period": {"type": "integer", "default": 14},
                "pivot_window": {"type": "integer", "default": 2},
                "min_gap_bars": {"type": "integer", "default": 3},
                "max_levels": {
                    "type": "integer",
                    "default": 5,
                    "description": "返回水平位数量，1到20之间",
                },
            },
            "required": ["klines"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        try:
            from apps.strategy_engine.indicators import horizontal_key_levels

            data = horizontal_key_levels(
                klines=kwargs.get("klines"),
                current_price=kwargs.get("current_price"),
                atr_period=int(kwargs.get("atr_period", 14)),
                pivot_window=int(kwargs.get("pivot_window", 2)),
                min_gap_bars=int(kwargs.get("min_gap_bars", 3)),
                max_levels=int(kwargs.get("max_levels", 5)),
            )
            return ToolResult(success=True, data=data)
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
