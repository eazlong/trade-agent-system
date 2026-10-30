"""3SMMA + Williams Fractal + RSI(50 中轴) 顺势剥头皮策略（5m）。

规则来源：外部视频教程（5 分钟级别顺势剥头皮），三个指标组合：
  ① 3SMMA（21 / 50 / 200 平滑均线）判定大趋势：多头排列只做多，空头排列只做空；
  ② Williams Fractals（period=2）给出回调后的入场触发（三角形箭头）；
  ③ RSI(14) 以 50 为多空分水岭：>50 才允许做多，<50 才允许做空。

本实现必须自己对齐的三条**关键机制**（写错任何一条都会让回测虚高）：

1. **分形天然滞后 2 根 K 线**。TradingView 的 Fractals 不是分形那根 K 线一收盘就
   出箭头，而要等右侧再走出 2 根完整 K 线才确认。所以「信号 K 线」= 分形 K 线
   i，「关键 K 线」= i+2。本策略在 bar t 只读 ``fractal_at(t-2)``，并且分形判定
   用的是 ``t-4..t`` 这 5 根**已收盘** K 线——按 bar t 的收盘价入场时，t 刚刚
   收盘，i=t-2 的左右各 2 根（t-1、t）也已收盘。这样既复刻了视频的滞后 2 根，
   又不含任何未来函数。

2. **延迟入场**。若分形当根与其确认根（i+2）的 RSI 都还在 50 的错误一侧，则继续
   等待；直到后续某根 K 线收线后 RSI 站到正确一侧，才把那根 K 线当「关键 K 线」
   并在其收盘价入场（视频案例里是箭头后第 4 根）。等待期有失效条件（结构破位 /
   趋势失效 / 超时 / 出现更新的同向分形），否则一个早已失效的水平会拖到几十根
   K 线之后才成交。

3. **趋势与震荡过滤**。均线空头排列时忽略绿色看涨分形，多头排列时忽略红色看跌
   分形；三条均线缠绕（无排列 / 排列但间距过窄）视为震荡，该区间所有分形一律
   放弃。

出场：止损放在关键 K 线低点（多）/ 高点（空）之外，止盈二选一——
``tp_mode="rr"`` 固定盈亏比（默认 1:2），或 ``tp_mode="ma"`` 右侧离场（收盘跌回
``exit_ma`` 之下 / 涨回之上）。另有 ``max_hold_bars`` 兜底，避免状态机卡死。

**成交价口径（回测诚实性，务必读）**：本仓库 BacktestEngine 只在**收盘价**成交
（``_process_signal`` 用 ``kline["close"]``），没有盘中撮合。所以：

- 默认 ``intrabar_exit=False``：全部出场都按「收盘价触发 + 收盘价成交」判定，与
  引擎口径逐位一致，收益不含「盘中触及止损却能以更好的价格成交」的偏差。
- 打开 ``intrabar_exit=True`` 时，策略在当根 K 线的 low/high 触及止损/止盈即发
  平仓信号，并把 ``stop_price``/``target_price``/``fill_hint`` 写进 metadata，
  供具备盘中撮合的模拟器按触发价（止损/止盈价）成交；本仓库引擎读不到这些字段，
  会退化成收盘价成交——这是刻意保留的：同一份策略代码，两套成交口径的结果可以
  直接对比，而不是让策略偷偷假设一种撮合。

空头需要引擎侧能力开关：``supports_short=True`` 的策略只有在
``STRATEGY_ENGINE_ALLOW_SHORT`` 打开时才会被 ``StrategyRegistry`` 注册（见
apps/strategy_engine/registry.py 的准入闸门），且要求标的是永续合约、
交易所侧允许双向持仓。

部署位：``~/.tradelogx/strategies/``；仓库镜像：本文件（两份需逐字节一致，
由 apps/strategy_engine/apps.py 的 discover() 在启动时扫描部署位注册）。
"""

from __future__ import annotations

import logging
import math
from decimal import ROUND_DOWN, Decimal

from apps.strategy_engine.base import BaseStrategy, StrategyContext
from apps.strategy_engine.registry import register_strategy

logger = logging.getLogger(__name__)

# 往返交易成本的保守上界（5bp 佣金 + 2bp 滑点 × 2 腿 = 14bp，取 15bp）。
# 用途见 ``_enter`` 里 ``qty_cap`` 的分母说明：名义上限必须把成本算进去，
# 否则每笔都会超出可用现金、触发引擎的部分成交兜底。
_ROUND_TRIP_COST_UPPER_BOUND = 0.0015


# ─────────────────────────────────────────────────────────────────────────────
# 指标实现：全部增量更新
#
# 回测一次跑 20 万根 5m K 线，若每根都重算 SMMA/RSI 的整条序列，复杂度是 O(n²)
# （20 万根 × 20 万 = 4e10 次运算，跑不完）。这里每个指标都维护一个增量状态：
# 新 bar 到来时只在尾部推进一格，O(1)；只有发现历史被替换（实盘 history 被裁剪、
# 回测起点变化）时才整体重建一次。
# ─────────────────────────────────────────────────────────────────────────────


def _smma_step(prev: float | None, close: float, period: int) -> float | None:
    """SMMA（= Wilder RMA）单步递推：``(prev*(n-1) + close) / n``。

    ``prev is None`` 表示种子尚未建立，返回 None（由调用方在攒满 period 根时用
    简单均值播种，与 TradingView 的 ``smoothed`` 首次取值一致）。
    """
    if prev is None:
        return None
    return (prev * (period - 1) + close) / period


def _rsi_step(
    avg_gain: float, avg_loss: float, prev_close: float, close: float, period: int
) -> tuple[float, float, float]:
    """Wilder RSI 单步：返回 ``(rsi, avg_gain, avg_loss)``。

    口径与 ``apps/signal_monitor/indicators.compute_rsi`` 逐位一致（同一种子
    ``mean(gains[:period])`` + 同一递推 ``(prev*(n-1)+x)/n``），因为实盘信号监控
    用的是那一份——两处口径分家会让回测和实盘看到不同的 RSI(50) 过滤结果。
    """
    delta = close - prev_close
    gain = delta if delta > 0 else 0.0
    loss = -delta if delta < 0 else 0.0
    avg_gain = (avg_gain * (period - 1) + gain) / period
    avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0, avg_gain, avg_loss
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs)), avg_gain, avg_loss


@register_strategy(name="smma_fractal_rsi_scalp_strategy")
class SmmaFractalRsiScalpStrategy(BaseStrategy):
    """3SMMA + Williams Fractal + RSI 顺势剥头皮。"""

    name = "smma_fractal_rsi_scalp_strategy"
    supports_short = True
    description = """策略类型：趋势跟踪（顺势回调剥头皮）
    核心指标：3SMMA(21/50/200) 排列 + Williams Fractals(period=2) + RSI(14) 50 中轴
    适用场景：深度好、参与者多的市场（加密货币永续/主流股市/外汇/期货）的 5 分钟级别趋势行情；均线缠绕的震荡区间主动放弃
    入场逻辑：均线多头排列时只取看涨分形（滞后 2 根确认），关键 K 线（分形后第 2 根，或 RSI 迟未过 50 时顺延至首个 RSI>50 的收线）收盘价做多；空头排列镜像做空"""

    min_kline_length = 260

    params_schema = {
        "ma_fast": {
            "type": "int",
            "default": 21,
            "min": 2,
            "description": "快线 SMMA 周期（视频：21）",
        },
        "ma_mid": {
            "type": "int",
            "default": 50,
            "min": 3,
            "description": "中线 SMMA 周期（视频：50）",
        },
        "ma_slow": {
            "type": "int",
            "default": 200,
            "min": 5,
            "description": "慢线 SMMA 周期（视频：200）",
        },
        "fractal_period": {
            "type": "int",
            "default": 2,
            "min": 1,
            "description": "Williams Fractals 左右各看几根（视频：2，即确认滞后 2 根）",
        },
        "rsi_period": {
            "type": "int",
            "default": 14,
            "min": 2,
            "description": "RSI 周期（视频：14）",
        },
        "rsi_midline": {
            "type": "float",
            "default": 50.0,
            "description": "RSI 多空分界线（视频：把上下轨都改成 50，即中轴）",
        },
        "min_trend_sep_pct": {
            "type": "float",
            "default": 0.0005,
            "min": 0.0,
            "description": "快慢线最小相对间距 |ma21-ma200|/ma200，低于此值视为均线缠绕（震荡）放弃交易；0=只看排列不看间距",
        },
        "tp_mode": {
            "type": "str",
            "default": "rr",
            "description": "止盈方式：rr=固定盈亏比，ma=右侧离场（收盘跌回/涨回 exit_ma 之外）",
        },
        "rr_ratio": {
            "type": "float",
            "default": 2.0,
            "min": 0.1,
            "description": "固定盈亏比止盈的盈亏比（视频：1:2）",
        },
        "exit_ma": {
            "type": "int",
            "default": 21,
            "min": 2,
            "description": "右侧离场参考均线周期（tp_mode=ma 时生效）",
        },
        "stop_buffer_pct": {
            "type": "float",
            "default": 0.0005,
            "min": 0.0,
            "description": "止损相对关键 K 线低/高点的缓冲比例，避免贴边被扫",
        },
        "max_hold_bars": {
            "type": "int",
            "default": 96,
            "min": 1,
            "description": "最长持仓 K 线根数（96 根 5m = 8 小时），超时按收盘价平仓防止状态机卡死",
        },
        "pending_max_wait_bars": {
            "type": "int",
            "default": 12,
            "min": 0,
            "description": "延迟入场最长等待根数（从确认根算起），超时作废该分形",
        },
        "risk_per_trade_pct": {
            "type": "float",
            "default": 0.01,
            "min": 0.0,
            "description": "单笔风险占总权益比例（按 入场价-止损价 反推数量）",
        },
        "max_position_pct": {
            "type": "float",
            "default": 1.0,
            "min": 0.0,
            "max": 1.0,
            "description": "单笔名义价值占权益上限（防止小止损把仓位放大到不现实）",
        },
        "intrabar_exit": {
            "type": "bool",
            "default": False,
            "description": "True=当根 low/high 触及止损/止盈即出场（并在 metadata 透出触发价供盘中撮合模拟器使用）；False=收盘价判定（与本仓库回测引擎口径一致）",
        },
    }

    def __init__(self, ctx: StrategyContext) -> None:
        super().__init__(ctx)
        p = self.ctx.params
        self.ma_fast = max(2, int(p.get("ma_fast", 21)))
        self.ma_mid = max(3, int(p.get("ma_mid", 50)))
        self.ma_slow = max(5, int(p.get("ma_slow", 200)))
        self.fractal_period = max(1, int(p.get("fractal_period", 2)))
        self.rsi_period = max(2, int(p.get("rsi_period", 14)))
        self.rsi_midline = float(p.get("rsi_midline", 50.0))
        self.min_trend_sep_pct = float(p.get("min_trend_sep_pct", 0.0005))
        self.tp_mode = str(p.get("tp_mode", "rr"))
        self.rr_ratio = float(p.get("rr_ratio", 2.0))
        self.exit_ma = max(2, int(p.get("exit_ma", 21)))
        self.stop_buffer_pct = float(p.get("stop_buffer_pct", 0.0005))
        self.max_hold_bars = max(1, int(p.get("max_hold_bars", 96)))
        self.pending_max_wait_bars = max(0, int(p.get("pending_max_wait_bars", 12)))
        self.risk_per_trade_pct = float(p.get("risk_per_trade_pct", 0.01))
        self.max_position_pct = float(p.get("max_position_pct", 1.0))
        self.intrabar_exit = bool(p.get("intrabar_exit", False))

        if self.tp_mode not in ("rr", "ma"):
            raise ValueError(f"tp_mode 必须是 'rr' 或 'ma'，收到 {self.tp_mode!r}")

        # ── 增量指标状态 ──
        self._n = 0  # 已处理到 history 的第几根（长度）
        self._last_ts: str = ""  # 已处理末根的 timestamp，用于识别「最后一根被替换」
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._smma: dict[int, list[float | None]] = {
            self.ma_fast: [],
            self.ma_mid: [],
            self.ma_slow: [],
            self.exit_ma: [],
        }
        self._rsi: list[float] = []
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._rsi_seeded = False

        # ── 持仓状态（止损/止盈/持仓根数）──
        self._side: str = ""  # "long" | "short" | ""
        self._entry_price = 0.0
        self._stop_price = 0.0
        self._target_price = 0.0  # tp_mode=rr 时有效，ma 模式为 0
        self._entry_index = -1

        # ── 待入场信号 ──
        # {"side": "long"/"short", "fractal_index": i, "confirm_index": i+N,
        #  "signal_low"/"signal_high": 分形 K 线极值}
        self._pending: dict | None = None

    # ── 指标状态推进 ────────────────────────────────────────────────────────

    def _sync_indicators(self, history: list[dict]) -> None:
        """把指标状态推进到 ``history`` 的末根。

        四种情形，只有情形 4（历史被改写）才付 O(n) 代价：

        1. 空状态 → 从头部整体构建；
        2. 新增了若干根 → 只对新增根增量推进（每根 O(1)，这是回测的主路径：
           回测每根都把 ``history`` 加长 1，20 万根若每根重算就是 O(n²)）；
        3. 长度与末根时间都没变 → 本根已处理，直接返回；
        4. 末根时间变了（实盘对**未收盘 bar** 的覆盖写）或长度回退（history 被
           裁剪）→ 整体重建，否则增量递推的种子会错位。
        """
        n = len(history)
        if n == 0:
            return
        last_ts = str(history[-1].get("timestamp", ""))
        if self._n == 0 or n < self._n:
            self._rebuild(history)
            return
        if n == self._n:
            if last_ts != self._last_ts:
                self._rebuild(history)
            return
        # n > self._n：上一根是否仍是同一根（被改写则整条序列作废重算）
        processed_ts = str(history[self._n - 1].get("timestamp", ""))
        if processed_ts != self._last_ts:
            self._rebuild(history)
            return
        for k in range(self._n, n):
            self._append_bar(history[k])

    def _rebuild(self, history: list[dict]) -> None:
        self._closes = []
        self._highs = []
        self._lows = []
        self._smma = {
            self.ma_fast: [],
            self.ma_mid: [],
            self.ma_slow: [],
            self.exit_ma: [],
        }
        self._rsi = []
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._rsi_seeded = False
        self._n = 0
        for kline in history:
            self._append_bar(kline)
        self._last_ts = str(history[-1].get("timestamp", "")) if history else ""

    def _append_bar(self, kline: dict) -> None:
        close = float(kline["close"])
        high = float(kline["high"])
        low = float(kline["low"])
        self._closes.append(close)
        self._highs.append(high)
        self._lows.append(low)
        idx = self._n
        self._n += 1
        self._last_ts = str(kline.get("timestamp", ""))

        for period, series in self._smma.items():
            if idx < period - 1:
                series.append(None)
            elif idx == period - 1:
                series.append(sum(self._closes[:period]) / period)
            else:
                prev = series[-1]
                # prev 为 None 只在 period=0 时可能，period>=2 时 idx>=period-1
                # 已有种子；这里仍保留 None 透传，避免静默用错误种子继续递推。
                series.append(
                    None if prev is None else (prev * (period - 1) + close) / period
                )

        # RSI：首值在 idx == rsi_period（与 compute_rsi 的 result[period] 对齐）
        if idx == self.rsi_period:
            deltas = [
                self._closes[i] - self._closes[i - 1]
                for i in range(1, self.rsi_period + 1)
            ]
            self._avg_gain = sum(d for d in deltas if d > 0) / self.rsi_period
            self._avg_loss = sum(-d for d in deltas if d < 0) / self.rsi_period
            self._rsi_seeded = True
            self._rsi.append(self._rsi_value())
        elif idx > self.rsi_period and self._rsi_seeded:
            rsi_value, self._avg_gain, self._avg_loss = _rsi_step(
                self._avg_gain,
                self._avg_loss,
                self._closes[-2],
                close,
                self.rsi_period,
            )
            self._rsi.append(rsi_value)
        else:
            self._rsi.append(float("nan"))

    def _rsi_value(self) -> float:
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ── 指标读取 ────────────────────────────────────────────────────────────

    def _ma(self, period: int, index: int) -> float | None:
        series = self._smma[period]
        if 0 <= index < len(series):
            return series[index]
        return None

    def _rsi_at(self, index: int) -> float | None:
        if 0 <= index < len(self._rsi):
            value = self._rsi[index]
            return None if math.isnan(value) else value
        return None

    def _fractal_low_at(self, index: int) -> bool:
        """``index`` 是否是看涨分形（该根 low 是左右各 N 根的严格最低）。

        右侧 N 根必须已存在——这正是「滞后 N 根确认」的实现：``index`` 只有在
        ``len-1 >= index+N`` 之后才可能返回 True。
        """
        n = self.fractal_period
        if index - n < 0 or index + n >= self._n:
            return False
        pivot = self._lows[index]
        for offset in range(1, n + 1):
            if not (
                pivot < self._lows[index - offset]
                and pivot < self._lows[index + offset]
            ):
                return False
        return True

    def _fractal_high_at(self, index: int) -> bool:
        """``index`` 是否是看跌分形（该根 high 是左右各 N 根的严格最高）。"""
        n = self.fractal_period
        if index - n < 0 or index + n >= self._n:
            return False
        pivot = self._highs[index]
        for offset in range(1, n + 1):
            if not (
                pivot > self._highs[index - offset]
                and pivot > self._highs[index + offset]
            ):
                return False
        return True

    def _regime(self, index: int) -> str:
        """``"long"`` / ``"short"`` / ``""``（震荡或数据不足）。

        多头排列 = ma_fast > ma_mid > ma_slow；空头排列镜像。另外要求快慢线有
        ``min_trend_sep_pct`` 的相对间距——三条均线贴在一起时「排列」只在数值上
        成立，价格随时穿插，那正是视频里要放弃的震荡区间。
        """
        fast = self._ma(self.ma_fast, index)
        mid = self._ma(self.ma_mid, index)
        slow = self._ma(self.ma_slow, index)
        if fast is None or mid is None or slow is None:
            return ""
        if not (slow > 0):
            return ""
        sep = abs(fast - slow) / slow
        if sep < self.min_trend_sep_pct:
            return ""
        if fast > mid > slow:
            return "long"
        if fast < mid < slow:
            return "short"
        return ""

    # ── 主循环 ──────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        """启动自检：``intrabar_exit=True`` 在本仓库口内是**静默降级**。

        ``_exit_signal`` 会把 ``fill_hint``（触发价）写进 metadata，但
        ``BacktestEngine._process_signal`` 恒用 ``kline["close"]`` 成交、读不到
        这个字段。于是打开 ``intrabar_exit`` 会让人以为在跑盘中撮合，实际跑的
        仍是收盘口径。这不是 bug（口径已在模块 docstring 说明），但**必须出声**，
        否则就是拿错误的假设做决策。
        """
        if self.intrabar_exit:
            engine_name = type(getattr(self, "_engine", None)).__name__
            logger.warning(
                "smma_fractal_rsi_scalp_strategy: intrabar_exit=True，但当前引擎"
                "（%s）只在收盘价成交，会忽略 metadata['fill_hint'] —— 实际仍是"
                "收盘口径。要真正按触发价成交，需具备盘中撮合的模拟器。",
                engine_name or "unknown",
            )

    def on_bar(self, kline: dict, history: list[dict]):
        self._sync_indicators(history)
        index = self._n - 1

        # 预热：慢线 + 分形右侧 N 根 + RSI 都需要数据。
        warmup = max(self.ma_slow, self.rsi_period + 1, self.fractal_period + 1)
        if index < warmup:
            return None

        position = self.ctx.position
        if position != 0:
            # 持仓真相以引擎 ctx 为准（策略账本与引擎账本分家是历史事故来源）。
            if (self._side == "long" and position < 0) or (
                self._side == "short" and position > 0
            ):
                # 方向与账本不一致：重置本地状态，跟随引擎。
                # 这里必须连 _pending 一起清：持仓态与本地账本已经分叉，
                # 任何挂起的入场信号都是在这一分叉下产生的，不可信。
                self._reset_position_state(clear_pending=True)
                return None
            if not self._side:
                # 重新认领一个「本地账本不知道」的持仓（实盘重启、部分成交、
                # 交易所侧强平后回补都可能出现）。此时必须**用引擎的
                # avg_entry_price 重建止损**：
                # 若只把 _stop_price 留成 0，多头 ``close <= 0`` 永假、
                # ``_target_price > 0`` 也永假 —— 该仓位只能靠 max_hold 或
                # tp_mode=ma 退出，止损形同虚设。回测里引擎账本干净触发不了，
                # 但实盘这是真实的资金风险。
                self._adopt_position(position, index)
                return self._manage_exit(kline, index)
            return self._manage_exit(kline, index)

        # 空仓：清掉残留状态（可能是另一侧的强平/外部干预）
        self._reset_position_state()
        return self._handle_flat(kline, index)

    def _manage_exit(self, kline: dict, index: int):
        high = float(kline["high"])
        low = float(kline["low"])
        close = float(kline["close"])
        held = index - self._entry_index

        # 硬止损：低点/高点触及止损价。intrabar_exit=False 时按收盘价判定（与
        # 本仓库引擎「只在收盘成交」的口径一致，避免虚构成交价）。
        if self.intrabar_exit:
            if self._side == "long" and low <= self._stop_price:
                return self._exit_signal("stop_loss", self._stop_price, kline)
            if self._side == "short" and high >= self._stop_price:
                return self._exit_signal("stop_loss", self._stop_price, kline)
        else:
            if self._side == "long" and close <= self._stop_price:
                return self._exit_signal("stop_loss", close, kline)
            if self._side == "short" and close >= self._stop_price:
                return self._exit_signal("stop_loss", close, kline)

        # 固定盈亏比止盈
        if self.tp_mode == "rr" and self._target_price > 0:
            if self.intrabar_exit:
                if self._side == "long" and high >= self._target_price:
                    return self._exit_signal("take_profit", self._target_price, kline)
                if self._side == "short" and low <= self._target_price:
                    return self._exit_signal("take_profit", self._target_price, kline)
            else:
                if self._side == "long" and close >= self._target_price:
                    return self._exit_signal("take_profit", close, kline)
                if self._side == "short" and close <= self._target_price:
                    return self._exit_signal("take_profit", close, kline)

        # 右侧离场：收盘回到 exit_ma 的错误一侧
        if self.tp_mode == "ma":
            exit_ma = self._ma(self.exit_ma, index)
            if exit_ma is not None:
                if self._side == "long" and close < exit_ma:
                    return self._exit_signal("ma_cross_exit", close, kline)
                if self._side == "short" and close > exit_ma:
                    return self._exit_signal("ma_cross_exit", close, kline)

        # 超时兜底：剥头皮策略不该持仓过夜，超时按收盘价平仓
        if held >= self.max_hold_bars:
            return self._exit_signal("max_hold_exit", close, kline)

        return None

    def _exit_signal(self, reason: str, fill_hint: float, kline: dict):
        """平仓信号。

        ``metadata`` 三条字段的作用：

        - ``reason``：本仓库回测引擎写 trade 记录时取 ``signal.metadata["reason"]``
          作为 ``exit_reason``（见 backtest_mode._apply_sell_fill），不传就全退化
          成 "signal"，按出场原因做的统计全部失效；
        - ``stop_price`` / ``target_price`` / ``fill_hint``：供具备盘中撮合的
          模拟器回填真实触发价（本仓库引擎只按收盘价成交，会忽略它们）。
        """
        signal = self.ctx.close_position(
            signal_name=reason,
        )
        if signal is None:
            return None
        signal.metadata = dict(signal.metadata or {})
        signal.metadata.update(
            {
                "reason": reason,
                "stop_price": self._stop_price,
                "target_price": self._target_price,
                "fill_hint": fill_hint,
                "bar_index": self._n - 1,
            }
        )
        return signal

    def _reset_position_state(self, *, clear_pending: bool = False) -> None:
        """清空持仓态。

        ``clear_pending`` 的默认值是 False，而**不是**无条件清理 ``_pending`` ——
        这个开关是一次回归修复的直接结果：

        ``on_bar`` 在**每个空仓 bar** 上都会调用本方法（清掉上一段持仓的残留
        止损/止盈）。若它同时清掉 ``_pending``，那么「延迟入场」在结构上永不
        可能发生：分形在 bar t 确认后，只要 t+1 仍是空仓，待入场信号就被抹掉，
        于是永远等不到 RSI 站回 50 的那根。实测（修复前）：5,585 笔入场里
        **延迟入场 0 笔**，全部 lag=0，而确认根 RSI 在错误一侧的比例是 38.5%
        —— 也就是说视频「箭头后第 4 根入场」那条规则一次都没被真正执行过。

        真正的「账本分叉后凭空开仓」风险来自**持仓态**混乱，而不是待入场信号，
        所以只在「持仓与本地方向冲突」「开始新的一段空仓」这两个明确时点显式
        传 ``clear_pending=True``。
        """
        self._side = ""
        self._entry_price = 0.0
        self._stop_price = 0.0
        self._target_price = 0.0
        self._entry_index = -1
        if clear_pending:
            self._pending = None

    def _adopt_position(self, position: Decimal, index: int) -> None:
        """重新认领引擎侧已有的持仓，并**重建止损/止盈**。

        止损优先用引擎的加权均价（``ctx.avg_entry_price``）按当前
        ``stop_buffer_pct`` 反推；均价不可用时退化为「当前收盘价 ± 一个
        ATR 代理」（这里用最近 ``fractal_period*2+1`` 根的极值），绝不留下
        0 止损 —— 那等于没有风控。
        """
        self._side = "long" if position > 0 else "short"
        self._entry_index = index
        self._pending = None

        avg = self.ctx.avg_entry_price
        entry = float(avg) if avg and float(avg) > 0 else self._closes[-1]
        self._entry_price = entry
        if self.fractal_period > 0 and self._n > 0:
            lookback_start = max(0, self._n - (2 * self.fractal_period + 1))
            swings = (
                self._lows[lookback_start:]
                if self._side == "long"
                else self._highs[lookback_start:]
            )
            ref = min(swings) if self._side == "long" else max(swings)
        else:
            ref = entry * (1 - 0.005) if self._side == "long" else entry * (1 + 0.005)

        if self._side == "long":
            stop = min(ref, entry) * (1 - self.stop_buffer_pct)
            self._stop_price = stop
            self._target_price = (
                entry + self.rr_ratio * (entry - stop)
                if self.tp_mode == "rr" and entry > stop
                else 0.0
            )
        else:
            stop = max(ref, entry) * (1 + self.stop_buffer_pct)
            self._stop_price = stop
            self._target_price = (
                entry - self.rr_ratio * (stop - entry)
                if self.tp_mode == "rr" and stop > entry
                else 0.0
            )

    # ── 空仓：分形 → 关键 K 线 → 入场 ──────────────────────────────────────

    def _handle_flat(self, kline: dict, index: int):
        regime = self._regime(index)
        close = float(kline["close"])
        rsi = self._rsi_at(index)

        # ① 先检查「待入场」信号是否顺延成交（视频里的延迟入场）
        if self._pending is not None:
            signal = self._try_pending_entry(kline, index, regime, rsi, close)
            if signal is not None:
                return signal

        # 若已有待入场信号，本根不再采集新分形——避免同一回调上重复排队。
        if self._pending is not None:
            return None

        # ② 采集在 bar t 刚被确认的分形（分形 K 线 = t - fractal_period）
        signal_index = index - self.fractal_period
        if signal_index < self.fractal_period:
            return None
        if regime == "long" and self._fractal_low_at(signal_index):
            self._pending = {
                "side": "long",
                "fractal_index": signal_index,
                "confirm_index": index,
                "signal_extreme": self._lows[signal_index],
            }
            return self._try_pending_entry(kline, index, regime, rsi, close)
        if regime == "short" and self._fractal_high_at(signal_index):
            self._pending = {
                "side": "short",
                "fractal_index": signal_index,
                "confirm_index": index,
                "signal_extreme": self._highs[signal_index],
            }
            return self._try_pending_entry(kline, index, regime, rsi, close)
        return None

    def _try_pending_entry(
        self,
        kline: dict,
        index: int,
        regime: str,
        rsi: float | None,
        close: float,
    ):
        """待入场信号 → 入场信号 / 作废。

        可成交条件（全部满足）：
        1. 趋势仍在同一侧（趋势翻转即作废）；
        2. 本根 K 线收盘后 RSI 站在正确一侧（>50 多 / <50 空）——视频的「RSI
           过滤」，也是延迟入场的判据；
        3. 结构未破：多头要求收盘未跌破分形 K 线低点（否则这次回调已经失败）。

        这三个判据都只用**当根已收盘**的数据，无未来函数。
        """
        pending = self._pending
        if pending is None:
            return None
        side = pending["side"]

        if (side == "long" and regime != "long") or (
            side == "short" and regime != "short"
        ):
            # 趋势失效（含进入震荡）：该分形作废
            self._pending = None
            return None

        waited = index - pending["confirm_index"]
        if waited > self.pending_max_wait_bars:
            self._pending = None
            return None

        if rsi is None:
            return None

        if side == "long":
            if rsi <= self.rsi_midline:
                return None
            if close <= pending["signal_extreme"]:
                # 收盘已跌破分形低点：结构破位，不再等待
                self._pending = None
                return None
            stop_ref = min(pending["signal_extreme"], float(kline["low"]))
            stop = stop_ref * (1 - self.stop_buffer_pct)
            return self._enter("long", kline, index, stop)
        else:
            if rsi >= self.rsi_midline:
                return None
            if close >= pending["signal_extreme"]:
                self._pending = None
                return None
            stop_ref = max(pending["signal_extreme"], float(kline["high"]))
            stop = stop_ref * (1 + self.stop_buffer_pct)
            return self._enter("short", kline, index, stop)

    def _enter(self, side: str, kline: dict, index: int, stop: float):
        fractal_index = self._pending["fractal_index"] if self._pending else -1
        close = float(kline["close"])
        entry = close
        risk_per_unit = (entry - stop) if side == "long" else (stop - entry)
        if risk_per_unit <= 0:
            self._pending = None
            return None

        equity = self.ctx.to_portfolio_context().total_capital
        if equity <= 0:
            self._pending = None
            return None

        # 风险反推数量，再用名义价值上限封顶：小止损会把仓位放大到不现实的
        # 规模（回测里多头受现金约束、空头受保证金约束，会变成部分成交——
        # 那不是策略的意图，是没做仓位约束的后果）。
        qty_risk = float(equity) * self.risk_per_trade_pct / risk_per_unit

        # 名义上限的分母必须含交易成本。引擎在名义金额之外还要加佣金与滑点
        # （backtest_mode._apply_buy_fill：total_cost = qty*(1+slip)+commission），
        # 若分母只写 ``entry``，则 ``qty_cap`` 换算出的名义刚好等于权益，加成本后
        # 必然超出现金 —— 于是**每一笔**都落进引擎的「现金不足 → 部分成交」兜底，
        # 仓位被静默改小、日志被刷屏，且 ``risk_per_trade_pct`` 这句描述在回测里
        # 根本不成立（实测 96% 的交易由名义上限而非风险预算决定数量）。
        # 费率无法从 ctx 读到（引擎不往外暴露），因此用一个略保守的常数上界：
        # 5bp 佣金 + 2bp 滑点 × 2 腿 ≈ 0.14%，取 0.0015 留一点余量。
        cost_buffer = 1.0 + _ROUND_TRIP_COST_UPPER_BOUND
        qty_cap = (
            float(equity) * self.max_position_pct / (entry * cost_buffer)
            if entry > 0
            else 0.0
        )
        qty = min(qty_risk, qty_cap)
        # 数量**只能向下取整**，绝不能四舍五入。用 ROUND_HALF_UP 时，
        # 0.0006124 → 0.000613 会多买 0.1%，把名义上限仅剩的 0.15% 成本余量
        # 吃掉，于是 ``cost + commission`` 又超出现金，重新落回引擎的部分成交
        # 兜底。实测（权益衰减到 $70 量级时）这一项单独造成 289 笔部分成交。
        # 向下取整只会让仓位比你要求的更小，永远不会超额，是唯一正确的方向。
        qty_dec = Decimal(str(qty)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if qty_dec <= 0:
            self._pending = None
            return None

        if self.tp_mode == "rr":
            target = (
                entry + self.rr_ratio * (entry - stop)
                if side == "long"
                else entry - self.rr_ratio * (stop - entry)
            )
        else:
            target = 0.0

        self._side = side
        self._entry_price = entry
        self._stop_price = stop
        self._target_price = target
        self._entry_index = index
        self._pending = None

        metadata = {
            "reason": f"fractal_{side}_entry",
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "fractal_index": fractal_index,
            "risk_per_unit": risk_per_unit,
            "rr_ratio": self.rr_ratio if self.tp_mode == "rr" else 0.0,
        }
        if side == "long":
            return self.ctx.buy(
                qty_dec, signal_name=f"fractal_{side}_entry", metadata=metadata
            )
        return self.ctx.sell(
            qty_dec, signal_name=f"fractal_{side}_entry", metadata=metadata
        )
