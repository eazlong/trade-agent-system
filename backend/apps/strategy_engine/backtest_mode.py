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
        self._position += signal.quantity

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

        # 计算平均成本（简化：假设 FIFO）
        avg_cost = Decimal(str(kline.get("open", fill_price)))  # 简化处理
        pnl = (fill_price - avg_cost) * sell_qty - commission

        self._cash += net_proceeds
        self._position -= sell_qty

        # 记录交易
        self._trades.append(
            {
                "entry_time": kline.get("timestamp", ""),
                "exit_time": kline.get("timestamp", ""),
                "side": "long",  # 简化：假设只做多
                "entry_price": avg_cost,
                "exit_price": fill_price,
                "quantity": sell_qty,
                "pnl": pnl,
                "pnl_pct": float(pnl / (avg_cost * sell_qty))
                if avg_cost * sell_qty > 0
                else 0,
                "commission": commission,
                "signal": signal.signal_name,
                "exit_reason": signal.metadata.get("reason", "signal"),
            }
        )

    def _close_position(self, kline: dict, reason: str) -> None:
        """强制平仓"""
        if self._position <= 0:
            return

        fill_price = Decimal(str(kline["close"]))
        proceeds = self._position * fill_price
        commission = proceeds * self.commission_rate
        net_proceeds = proceeds - commission
        avg_cost = Decimal(str(kline.get("open", fill_price)))
        pnl = (fill_price - avg_cost) * self._position - commission

        self._cash += net_proceeds

        self._trades.append(
            {
                "entry_time": kline.get("timestamp", ""),
                "exit_time": kline.get("timestamp", ""),
                "side": "long",
                "entry_price": avg_cost,
                "exit_price": fill_price,
                "quantity": self._position,
                "pnl": pnl,
                "pnl_pct": float(pnl / (avg_cost * self._position))
                if avg_cost * self._position > 0
                else 0,
                "commission": commission,
                "signal": "close_position",
                "exit_reason": reason,
            }
        )
        self._position = Decimal("0")

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

        # 胜率
        winning_trades = [t for t in self._trades if t.get("pnl", 0) > 0]
        total_closed = len(self._trades)
        win_rate = len(winning_trades) / total_closed if total_closed > 0 else None

        # 夏普比率（简化：用日收益率）
        sharpe = self._compute_sharpe()

        return {
            "final_equity": final_equity,
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
) -> dict:
    """
    将回测结果存入数据库。

    Returns:
        {"result_id": str, "trade_count": int}
    """
    from apps.backtest.models import BacktestResult, BacktestTrade
    from apps.trading.models import Strategy

    @sync_to_async
    def _save():
        strategy = Strategy.objects.get(id=strategy_id)

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
            equity_curve=stats.get("equity_curve", [])[:2000],  # 限制点数
            drawdown_curve=[
                {"timestamp": p["timestamp"], "drawdown": p["drawdown"]}
                for p in stats.get("equity_curve", [])[:2000]
            ],
            ohlcv_data=ohlcv_data[:500] if ohlcv_data else [],  # 采样存储
        )

        # 保存交易明细
        trades_to_create = []
        for trade_data in stats.get("trades", []):
            trades_to_create.append(
                BacktestTrade(
                    backtest=result,
                    entry_time=datetime.fromisoformat(trade_data["entry_time"])
                    if isinstance(trade_data["entry_time"], str)
                    else trade_data["entry_time"],
                    exit_time=datetime.fromisoformat(trade_data["exit_time"])
                    if isinstance(trade_data["exit_time"], str)
                    else trade_data["exit_time"],
                    side=trade_data["side"],
                    entry_price=trade_data["entry_price"],
                    exit_price=trade_data["exit_price"],
                    quantity=trade_data["quantity"],
                    pnl=trade_data.get("pnl"),
                    pnl_pct=trade_data.get("pnl_pct"),
                    commission=trade_data.get("commission", 0),
                    signal=trade_data.get("signal", ""),
                    exit_reason=trade_data.get("exit_reason", ""),
                )
            )

        BacktestTrade.objects.bulk_create(trades_to_create)

        return {"result_id": str(result.id), "trade_count": len(trades_to_create)}

    return await _save()
