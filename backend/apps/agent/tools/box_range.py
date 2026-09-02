"""Box range detection tool for trading agents."""

from __future__ import annotations

from .base import BaseTool, ToolResult


class DetectBoxRangeTool(BaseTool):
    """判断指定 K 线窗口是否处于箱体震荡，并返回上下边界。"""

    name = "detect_box_range"
    description = (
        "判断传入的K线数据是否处于箱体震荡（价格在指定时间区间内反复触及上下边界），"
        "并返回上下边界价格。判断条件：上下边界必须同时满足 "
        "局部极值点数 >= min_pivots 且被K线触碰次数 >= min_touches，"
        "且整体宽度不超过 max_width_abs / max_width_pct 阈值。"
        "只做纯计算，不获取行情。"
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
                "max_width_abs": {
                    "type": "number",
                    "description": "震荡区间绝对宽度上限（价格单位），与 max_width_pct 二选一必填",
                },
                "max_width_pct": {
                    "type": "number",
                    "description": "震荡区间相对宽度上限（占中线比例，如 0.03 表示 3%），与 max_width_abs 二选一必填",
                },
                "pivot_window": {
                    "type": "integer",
                    "default": 2,
                    "description": "识别局部高/低点的左右各需多少根K线",
                },
                "min_gap_bars": {
                    "type": "integer",
                    "default": 3,
                    "description": "同一边界相邻两次触碰至少间隔多少根K线",
                },
                "min_pivots": {
                    "type": "integer",
                    "default": 2,
                    "description": "上下边界至少由多少个局部极值点聚合而成",
                },
                "min_touches": {
                    "type": "integer",
                    "default": 2,
                    "description": "上下边界至少被K线触碰多少次",
                },
                "atr_period": {
                    "type": "integer",
                    "default": 14,
                    "description": "用于推导触碰容差的 ATR 周期",
                },
            },
            "required": ["klines"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        try:
            from apps.strategy_engine.indicators import detect_box_range

            data = detect_box_range(
                klines=kwargs.get("klines"),
                max_width_abs=kwargs.get("max_width_abs"),
                max_width_pct=kwargs.get("max_width_pct"),
                pivot_window=int(kwargs.get("pivot_window", 2)),
                min_gap_bars=int(kwargs.get("min_gap_bars", 3)),
                min_pivots=int(kwargs.get("min_pivots", 2)),
                min_touches=int(kwargs.get("min_touches", 2)),
                atr_period=int(kwargs.get("atr_period", 14)),
            )
            return ToolResult(success=True, data=data)
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
