"""
回测引擎 — 历史数据回放

加载策略代码，逐 bar 调用 strategy.on_bar()，
模拟成交并记录交易明细，计算统计指标后存入 DB。
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from .base import BaseStrategy, OrderSignal

logger = logging.getLogger(__name__)

# 默认手续费率
DEFAULT_COMMISSION_RATE = Decimal("0.001")  # 0.1%


def _parse_dt(value: str | None) -> datetime | None:
    """将 ISO 时间字符串转为 timezone-aware datetime"""
    if not value or not isinstance(value, str):
        return None
    dt = datetime.fromisoformat(value)
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt)


class BacktestEngine:
    """回测引擎：历史数据回放"""

    def __init__(
        self,
        strategy: "BaseStrategy",
        ohlcv_data: list[dict],
        initial_capital: Decimal,
        symbol: str,
        timeframe: str,
        commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
    ):
        self.strategy = strategy
        self.ohlcv_data = ohlcv_data
        self.initial_capital = initial_capital
        self.symbol = symbol
        self.timeframe = timeframe
        self.commission_rate = commission_rate

        # 运行状态
        self._cash = initial_capital
        self._position = Decimal("0")
        self._avg_entry_price = Decimal("0")
        self._entry_time = ""  # 开仓时间（用于单次开仓/平仓的回撤兼容）
        self._entry_times: list[
            tuple[Decimal, str]
        ] = []  # [(数量, 开仓时间), ...] 分批建仓追踪
        self._trades: list[dict] = []
        self._equity_curve: list[dict] = []
        self._peak_equity = initial_capital

    async def run(self) -> dict:
        """
        执行回测。

        Returns:
            回测统计结果字典
        """
        self.strategy.on_start()

        for i, kline in enumerate(self.ohlcv_data):
            history = self.ohlcv_data[: i + 1]
            signal = self.strategy.on_bar(kline, history)

            if signal:
                self._process_signal(signal, kline)

            # 记录权益点
            current_equity = self._cash + self._position_value(kline)
            self._peak_equity = max(self._peak_equity, current_equity)
            drawdown = (
                float((current_equity - self._peak_equity) / self._peak_equity)
                if self._peak_equity > 0
                else 0.0
            )
            self._equity_curve.append(
                {
                    "timestamp": kline.get("timestamp", ""),
                    "equity": float(current_equity),
                    "drawdown": drawdown,
                }
            )

        self.strategy.on_stop()

        # 如果还有持仓，按最后一根 K 线收盘价平仓
        if self._position > 0 and self.ohlcv_data:
            last_kline = self.ohlcv_data[-1]
            self._close_position(last_kline, "end_of_backtest")

        return self._compute_stats()

    def _process_signal(self, signal: "OrderSignal", kline: dict) -> None:
        """处理策略信号，模拟成交"""
        # 使用 K 线收盘价作为成交价（简化市价单模拟）
        fill_price = Decimal(str(kline["close"]))

        if signal.side == "buy":
            self._execute_buy(signal, fill_price, kline)
        elif signal.side == "sell":
            self._execute_sell(signal, fill_price, kline)

    def _execute_buy(
        self, signal: "OrderSignal", fill_price: Decimal, kline: dict
    ) -> None:
        """模拟买入成交"""
        cost = signal.quantity * fill_price
        commission = cost * self.commission_rate
        total_cost = cost + commission

        if total_cost > self._cash:
            # 资金不足，部分成交或跳过
            logger.warning(
                "Backtest: insufficient cash for buy signal. "
                f"need={total_cost}, cash={self._cash}"
            )
            # 计算最大可买数量
            max_qty = self._cash / (fill_price * (1 + self.commission_rate))
            if max_qty <= 0:
                return
            signal = signal.__class__(
                side="buy",
                quantity=max_qty,
                order_type=signal.order_type,
                price=fill_price,
                signal_name=signal.signal_name,
                exchange=signal.exchange,
                metadata=signal.metadata,
            )
            cost = max_qty * fill_price
            commission = cost * self.commission_rate
            total_cost = cost + commission

        self._cash -= total_cost

        # 更新加权平均入场价（按成交金额加权）
        old_cost_basis = self._avg_entry_price * self._position
        self._position += signal.quantity
        new_cost_basis = old_cost_basis + cost
        self._avg_entry_price = (
            new_cost_basis / self._position if self._position > 0 else Decimal("0")
        )

        # 记录开仓时间（FIFO 队列）
        entry_ts = kline.get("timestamp", "")
        self._entry_times.append((signal.quantity, entry_ts))
        self._entry_time = entry_ts  # 兼容旧逻辑

        # 记录买入订单（开仓/加仓）
        trade_type = "open" if old_cost_basis == 0 else "add"
        self._trades.append(
            {
                "entry_time": entry_ts,
                "exit_time": None,
                "side": "long",
                "entry_price": float(fill_price),
                "exit_price": None,
                "quantity": float(signal.quantity),
                "pnl": None,
                "pnl_pct": None,
                "commission": float(commission),
                "signal": signal.signal_name,
                "exit_reason": "",
                "trade_type": trade_type,
            }
        )

        # 同步回策略上下文，供策略后续判断使用
        self.strategy.ctx.balance = self._cash
        self.strategy.ctx.position = self._position
        self.strategy.ctx.avg_entry_price = self._avg_entry_price

    def _execute_sell(
        self, signal: "OrderSignal", fill_price: Decimal, kline: dict
    ) -> None:
        """模拟卖出成交"""
        sell_qty = min(signal.quantity, self._position)
        if sell_qty <= 0:
            return

        proceeds = sell_qty * fill_price
        commission = proceeds * self.commission_rate
        net_proceeds = proceeds - commission

        # 计算平均成本（使用引擎追踪的加权平均入场价）
        avg_cost = (
            self._avg_entry_price
            if self._avg_entry_price > 0
            else Decimal(str(kline.get("open", fill_price)))
        )
        pnl = (fill_price - avg_cost) * sell_qty - commission

        # FIFO 匹配：从最早的开仓记录中获取 entry_time
        exit_time = kline.get("timestamp", "")
        entry_time_for_trade = self._get_fifo_entry_time(sell_qty)

        self._cash += net_proceeds
        self._position -= sell_qty

        # 同步回策略上下文
        self.strategy.ctx.balance = self._cash
        self.strategy.ctx.position = self._position
        self.strategy.ctx.avg_entry_price = (
            self._avg_entry_price if self._position > 0 else Decimal("0")
        )
        self._trades.append(
            {
"entry_time": entry_time_for_trade,
                "exit_time": exit_time,
                "side": "long",
                "entry_price": float(avg_cost),
                "exit_price": float(fill_price),
                "quantity": float(sell_qty),
                "pnl": float(pnl),
                "pnl_pct": float(pnl / (avg_cost * sell_qty))
                if avg_cost * sell_qty > 0
                else 0,
                "commission": float(commission),
                "signal": signal.signal_name,
                "exit_reason": signal.metadata.get("reason", "signal"),
                "trade_type": "close",
            }
        )

    def _get_fifo_entry_time(self, sell_qty: Decimal) -> str:
        """FIFO 匹配：获取卖出部分对应的最早开仓时间"""
        remaining = sell_qty
        entry_time = self._entry_time  # fallback

        i = 0
        while i < len(self._entry_times) and remaining > 0:
            qty, ts = self._entry_times[i]
            if qty <= remaining:
                # 完全消耗这一笔
                entry_time = ts
                remaining -= qty
                self._entry_times.pop(i)
                # 不增加 i，因为 pop 后下一个元素移到当前位置
            else:
                # 部分消耗这一笔
                entry_time = ts
                self._entry_times[i] = (qty - remaining, ts)
                remaining = Decimal("0")

        return entry_time

    def _close_position(self, kline: dict, reason: str) -> None:
        """强制平仓"""
        if self._position <= 0:
            return

        fill_price = Decimal(str(kline["close"]))
        proceeds = self._position * fill_price
        commission = proceeds * self.commission_rate
        net_proceeds = proceeds - commission
        avg_cost = (
            self._avg_entry_price
            if self._avg_entry_price > 0
            else Decimal(str(kline.get("open", fill_price)))
        )
        pnl = (fill_price - avg_cost) * self._position - commission

        self._cash += net_proceeds

        # FIFO 匹配：获取平仓对应的最早开仓时间
        entry_time_for_close = self._get_fifo_entry_time(self._position)

        self._trades.append(
            {
                "entry_time": entry_time_for_close,
                "exit_time": kline.get("timestamp", ""),
                "side": "long",
"entry_price": float(avg_cost),
                "exit_price": float(fill_price),
                "quantity": float(self._position),
                "pnl": float(pnl),
                "pnl_pct": float(pnl / (avg_cost * self._position))
                if avg_cost * self._position > 0
                else 0,
                "commission": float(commission),
                "signal": "close_position",
                "exit_reason": reason,
                "trade_type": "close",
            }
        )

        self._position = Decimal("0")
        self._avg_entry_price = Decimal("0")
        self._entry_time = ""
        self._entry_times = []

        # 平仓后同步回策略上下文
        self.strategy.ctx.balance = self._cash
        self.strategy.ctx.position = self._position
        self.strategy.ctx.avg_entry_price = self._avg_entry_price

    def _position_value(self, kline: dict) -> Decimal:
        """当前持仓市值"""
        if self._position <= 0:
            return Decimal("0")
        price = Decimal(str(kline["close"]))
        return self._position * price

    def _compute_stats(self) -> dict:
        """计算回测统计指标"""
        final_equity = self._cash + (
            self._position * Decimal(str(self.ohlcv_data[-1]["close"]))
            if self.ohlcv_data and self._position > 0
            else Decimal("0")
        )

        total_return = float(
            ((final_equity - self.initial_capital) / self.initial_capital) * 100
        )

        # 最大回撤
        max_drawdown = 0.0
        peak = self.initial_capital
        for point in self._equity_curve:
            equity = Decimal(str(point["equity"]))
            if equity > peak:
                peak = equity
            dd = float((equity - peak) / peak) if peak > 0 else 0
            if dd < max_drawdown:
                max_drawdown = dd

# 胜率（仅统计已平仓的交易）
        closed_trades = [t for t in self._trades if t.get("pnl") is not None]
        winning_trades = [t for t in closed_trades if t["pnl"] > 0]
        total_closed = len(closed_trades)
        win_rate = len(winning_trades) / total_closed if total_closed > 0 else None

        # 夏普比率（简化：用日收益率）
        sharpe = self._compute_sharpe()

        return {
            "final_equity": float(final_equity),
            "total_return_pct": total_return,
            "max_drawdown_pct": max_drawdown * 100,
            "win_rate": win_rate,
            "sharpe_ratio": sharpe,
            "total_trades": total_closed,
            "equity_curve": self._equity_curve,
            "trades": self._trades,
        }

    def _compute_sharpe(self) -> float | None:
        """计算夏普比率（简化版）"""
        if len(self._equity_curve) < 2:
            return None

        returns = []
        for i in range(1, len(self._equity_curve)):
            prev = self._equity_curve[i - 1]["equity"]
            curr = self._equity_curve[i]["equity"]
            if prev > 0:
                returns.append((curr - prev) / prev)

        if not returns:
            return None

        import numpy as np

        ret_arr = np.array(returns)
        mean_ret = np.mean(ret_arr)
        std_ret = np.std(ret_arr)

        if std_ret == 0:
            return None

        # 年化夏普（假设日频率）
        annual_factor = np.sqrt(252)
        return float((mean_ret / std_ret) * annual_factor)


async def _resolve_strategy_id(strategy_name: str) -> str | None:
    """根据策略名称查找或创建 Strategy 模型，返回 UUID 字符串。"""
    from asgiref.sync import sync_to_async
    from apps.trading.models import Strategy

    @sync_to_async
    def _get_or_create():
        obj, created = Strategy.objects.get_or_create(
            name=strategy_name,
            defaults={
                "code_path": f"strategies/{strategy_name}.py",
                "is_active": False,
            },
        )
        if created:
            logger.info(
                f"[BacktestMode] created Strategy model: {obj.id} name={strategy_name}"
            )
        return str(obj.id)

    try:
        return await _get_or_create()
    except Exception as e:
        logger.error(
            f"[BacktestMode] failed to resolve Strategy for '{strategy_name}': {e}"
        )
        return None


async def _get_strategy_id_from_result(result_id: str) -> str | None:
    """从 BacktestResult 获取关联的 Strategy UUID。"""
    from asgiref.sync import sync_to_async
    from apps.backtest.models import BacktestResult

    @sync_to_async
    def _lookup():
        result = BacktestResult.objects.get(id=result_id)
        return str(result.strategy_id)

    try:
        return await _lookup()
    except Exception as e:
        logger.error(
            f"[BacktestMode] failed to get strategy_id from result '{result_id}': {e}"
        )
        return None


def create_empty_result(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: Decimal,
    parameters: dict,
    user_id: str | None = None,
) -> str | None:
    """
    提前创建空的回测结果记录，供前端实时展示运行状态。

    Sync version — call directly from Celery tasks or other sync code.
    For async callers, use `create_empty_result_async` instead.

    Returns:
        BacktestResult UUID 字符串，失败时返回 None
    """
    from apps.backtest.models import BacktestResult
    from apps.trading.models import Strategy

    try:
        strategy = Strategy.objects.get(id=strategy_id)
        result = BacktestResult.objects.create(
            strategy=strategy,
            user_id=user_id,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date[:10] if len(start_date) > 10 else start_date,
            end_date=end_date[:10] if len(end_date) > 10 else end_date,
            initial_capital=initial_capital,
            # 占位值
            final_capital=initial_capital,
            total_return_pct=0.0,
            parameters=parameters,
        )
        return str(result.id)
    except Exception as e:
        logger.error(f"[BacktestMode] failed to create empty result: {e}")
        return None


async def create_empty_result_async(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: Decimal,
    parameters: dict,
    user_id: str | None = None,
) -> str | None:
    """Async version of create_empty_result — for callers in async context."""
    from asgiref.sync import sync_to_async
    from apps.backtest.models import BacktestResult
    from apps.trading.models import Strategy

    @sync_to_async
    def _create():
        strategy = Strategy.objects.get(id=strategy_id)
        result = BacktestResult.objects.create(
            strategy=strategy,
            user_id=user_id,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date[:10] if len(start_date) > 10 else start_date,
            end_date=end_date[:10] if len(end_date) > 10 else end_date,
            initial_capital=initial_capital,
            final_capital=initial_capital,
            total_return_pct=0.0,
            parameters=parameters,
        )
        return str(result.id)

    try:
        return await _create()
    except Exception as e:
        logger.error(f"[BacktestMode] failed to create empty result: {e}")
        return None


async def save_backtest_result(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: Decimal,
    parameters: dict,
    stats: dict,
    ohlcv_data: list[dict] | None = None,
    git_commit_hash: str = "",
    existing_result_id: str | None = None,
) -> dict:
    """
    将回测结果存入数据库（新建或更新已有记录）。

    Args:
        existing_result_id: 已有 BacktestResult 的 ID（提前创建的空记录）

    Returns:
        {"result_id": str, "trade_count": int}
    """
    from apps.backtest.models import BacktestResult, BacktestTrade
    from apps.trading.models import Strategy

    @sync_to_async
    def _save():
        strategy = Strategy.objects.get(id=strategy_id)

        if existing_result_id:
            # 更新已有记录
            result = BacktestResult.objects.get(id=existing_result_id)
            result.final_capital = stats["final_equity"]
            result.total_return_pct = stats["total_return_pct"]
            result.sharpe_ratio = stats["sharpe_ratio"]
            result.max_drawdown_pct = stats["max_drawdown_pct"]
            result.win_rate = stats["win_rate"]
            result.total_trades = stats["total_trades"]
            result.git_commit_hash = git_commit_hash
            result.parameters = parameters
            result.equity_curve = stats.get("equity_curve", [])[:2000]
            result.drawdown_curve = [
                {"timestamp": p["timestamp"], "drawdown": p["drawdown"]}
                for p in stats.get("equity_curve", [])[:2000]
            ]
            result.ohlcv_data = ohlcv_data if ohlcv_data else []
            result.indicator_data = (
                _compute_indicators(ohlcv_data) if ohlcv_data else {}
            )
            result.save()
        else:
            # 新建记录
            indicator_data = _compute_indicators(ohlcv_data) if ohlcv_data else {}
            result = BacktestResult.objects.create(
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                final_capital=stats["final_equity"],
                total_return_pct=stats["total_return_pct"],
                sharpe_ratio=stats["sharpe_ratio"],
                max_drawdown_pct=stats["max_drawdown_pct"],
                win_rate=stats["win_rate"],
                total_trades=stats["total_trades"],
                git_commit_hash=git_commit_hash,
                parameters=parameters,
                equity_curve=stats.get("equity_curve", [])[:2000],
                drawdown_curve=[
                    {"timestamp": p["timestamp"], "drawdown": p["drawdown"]}
                    for p in stats.get("equity_curve", [])[:2000]
                ],
                ohlcv_data=ohlcv_data if ohlcv_data else [],
                indicator_data=indicator_data,
            )

        # 保存交易明细：新建时直接创建；更新时先清理旧交易再重建
        if existing_result_id:
            # 更新已有记录，先删除旧交易
            BacktestTrade.objects.filter(backtest=result).delete()

        trades_to_create = []
        for trade_data in stats.get("trades", []):
            entry_time_val = trade_data.get("entry_time")
            exit_time_val = trade_data.get("exit_time")

            trades_to_create.append(
                BacktestTrade(
                    backtest=result,
                    entry_time=_parse_dt(entry_time_val),
                    exit_time=_parse_dt(exit_time_val),
                    side=trade_data["side"],
                    entry_price=trade_data["entry_price"],
                    exit_price=trade_data.get("exit_price"),
                    quantity=trade_data["quantity"],
                    pnl=trade_data.get("pnl"),
                    pnl_pct=trade_data.get("pnl_pct"),
                    commission=trade_data.get("commission", 0),
                    signal=trade_data.get("signal") or "",
                    exit_reason=trade_data.get("exit_reason") or "",
                    trade_type=trade_data.get("trade_type", "open"),
                )
            )
        BacktestTrade.objects.bulk_create(trades_to_create)

        return {"result_id": str(result.id), "trade_count": result.trades.count()}

    return await _save()


def _compute_indicators(ohlcv_data: list[dict]) -> dict:
    """
    计算技术指标并返回前端可消费的 JSON 格式。

    Returns:
        {
            "ma7": [...], "ma25": [...], "ma99": [...],
            "macd": {"dif": [...], "dea": [...], "hist": [...]},
            "rsi": [...]
        }
    """
    import numpy as np

    from apps.signal_monitor.indicators import (
        compute_macd,
        compute_rsi,
        compute_sma,
    )

    closes = np.array([k["close"] for k in ohlcv_data], dtype=np.float64)

    indicator_data: dict = {}

    # MA 均线
    for period, key in [(7, "ma7"), (25, "ma25"), (99, "ma99")]:
        sma = compute_sma(closes, period)
        # 将 NaN 替换为 None 以适配 JSON 序列化
        indicator_data[key] = [float(v) if not np.isnan(v) else None for v in sma]

    # MACD
    macd_result = compute_macd(closes)
    indicator_data["macd"] = {
        "dif": [float(v) if not np.isnan(v) else None for v in macd_result["macd"]],
        "dea": [float(v) if not np.isnan(v) else None for v in macd_result["signal"]],
        "hist": [
            float(v) if not np.isnan(v) else None for v in macd_result["histogram"]
        ],
    }

    # RSI
    rsi = compute_rsi(closes)
    indicator_data["rsi"] = [float(v) if not np.isnan(v) else None for v in rsi]

    return indicator_data
