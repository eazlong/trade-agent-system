"""Box range detection tool for trading agents."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class DetectBoxRangeTool(BaseTool):
    """判断指定 K 线窗口是否处于箱体震荡，并返回上下边界。"""

    name = "detect_box_range"
    description = (
        "判断传入的K线数据是否处于箱体震荡（价格在指定时间区间内反复触及上下边界），"
        "并返回上下边界价格及时间起止。判断条件：上下边界必须同时满足 "
        "局部极值点数 >= min_pivots 且被K线触碰次数 >= min_touches，"
        "且整体宽度不超过 max_width_abs / max_width_pct 阈值。"
        "为避免「局部紧簇冒充箱体」，算法会用窗口真实高/低做粗筛："
        "选出的上沿必须 >= window_high × (1 - upper_max_discard_pct)，"
        "下沿必须 <= window_low × (1 + lower_max_discard_pct)，"
        "默认值 0.15 意味着上沿至少够到窗口高点 85%、下沿最多只到窗口低点 115%"
        "（上下沿为独立约束，不保证跨幅覆盖 ≥70%）。"
        "box 返回的时间字段：start_index/end_index 分别为价格进入箱体的首根、"
        "仍在箱内的最后一根K线序号（进入 = 第一根 close 落入 "
        "[lower×(1-tol), upper×(1+tol)]；结束 = 最后一根 close 仍在箱内；"
        "判定仅用 close，影线穿透不算离箱），"
        "start_timestamp/end_timestamp 为对应时间戳，"
        "duration_bars 为跨度（中间离箱再回来算一整段），"
        "bars_in_box 为实际处于箱内的K线根数，"
        "duration_seconds 为尽力而为的时间差（秒）：数值 epoch 自动识别秒/毫秒、"
        "ISO 字符串可解析时计算；K线无 timestamp 或不可解析时为 None。"
        "started_in_box 表示窗口开头就已处于箱体（真实进入点早于窗口）；"
        "若窗口内没有任何 close 进入箱带，时间字段统一为 None。"
        "klines 可以直接传 K线 dict 列表（每项需含 high/low/close），"
        "也可以传 fetch_ohlcv 返回的 temp_file 路径字符串（工具会自动读取）。"
        "只做纯计算，不获取行情。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "klines": {
                    "description": (
                        "K线数据。两种形式任选其一："
                        "(a) K线 dict 列表，每项需包含 high、low、close，可包含 timestamp/open/volume；"
                        "(b) fetch_ohlcv 工具返回的 temp_file 路径字符串（工具会自动读取并解析）。"
                    ),
                    "anyOf": [
                        {"type": "array", "items": {"type": "object"}},
                        {"type": "string"},
                    ],
                },
                "max_width_abs": {
                    "type": "number",
                    "description": "震荡区间绝对宽度上限（价格单位），与 max_width_pct 二选一必填",
                },
                "max_width_pct": {
                    "type": "number",
                    "description": "震荡区间相对宽度上限（占中线比例，如 0.03 表示 3%），与 max_width_pct 二选一必填",
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
                "upper_max_discard_pct": {
                    "type": "number",
                    "default": 0.15,
                    "description": "上沿粗筛：选出的上沿必须 >= window_high × (1 - 该值)。0.15 = 至少够到窗口高点 85%",
                },
                "lower_max_discard_pct": {
                    "type": "number",
                    "default": 0.15,
                    "description": "下沿粗筛：选出的下沿必须 <= window_low × (1 + 该值)。0.15 = 最多只到窗口低点 115%",
                },
                "min_width_abs": {
                    "type": "number",
                    "description": "箱体绝对宽度下限（避免返回过窄的伪箱体）",
                },
                "min_width_pct": {
                    "type": "number",
                    "description": "箱体相对宽度下限（占中线比例，如 0.01 表示 1%）",
                },
            },
            "required": ["klines"],
        }

    @staticmethod
    def _coerce_klines(value) -> list[dict]:
        """Accept list[dict] (preferred) or a temp_file path string from fetch_ohlcv."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            path = Path(value)
            if not path.exists():
                raise ValueError(
                    f"klines 是字符串但路径不存在: {value}。"
                    "请直接传 K线 dict 列表，或先调用 fetch_ohlcv 拿到 temp_file"
                )
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"无法从 {value} 解析 K线 JSON: {exc}") from exc
            if not isinstance(records, list):
                raise ValueError(f"{value} 内容不是 K线列表（顶层需为数组）")
            return records
        raise ValueError(
            "klines 必须是 K线 dict 列表，或 fetch_ohlcv 返回的 temp_file 路径字符串"
        )

    async def execute(self, **kwargs) -> ToolResult:
        try:
            klines = self._coerce_klines(kwargs.get("klines"))
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        try:
            from apps.strategy_engine.indicators import detect_box_range

            data = detect_box_range(
                klines=klines,
                max_width_abs=kwargs.get("max_width_abs"),
                max_width_pct=kwargs.get("max_width_pct"),
                pivot_window=int(kwargs.get("pivot_window", 2)),
                min_gap_bars=int(kwargs.get("min_gap_bars", 3)),
                min_pivots=int(kwargs.get("min_pivots", 2)),
                min_touches=int(kwargs.get("min_touches", 2)),
                atr_period=int(kwargs.get("atr_period", 14)),
                upper_max_discard_pct=float(kwargs.get("upper_max_discard_pct", 0.15)),
                lower_max_discard_pct=float(kwargs.get("lower_max_discard_pct", 0.15)),
                min_width_abs=kwargs.get("min_width_abs"),
                min_width_pct=kwargs.get("min_width_pct"),
            )
            return ToolResult(success=True, data=data)
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
