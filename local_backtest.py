"""
本地高频交易策略回测脚本（不依赖 Docker/Django）
直接读取 JSON K 线数据并运行回测引擎。
"""

import json
import sys
import os
import asyncio
import numpy as np
from decimal import Decimal
from datetime import datetime

# 将策略引擎的基础模块添加到路径
# 我们需要 BaseStrategy, StrategyContext, OrderSignal 和指标函数
sys.path.insert(0, os.path.expanduser("~/Documents/code/blockchain/bot/qt_sys/trade_agent_sys/backend"))

# 手动导入所需模块，避免 Django 启动
from apps.strategy_engine.base import BaseStrategy, StrategyContext
from apps.strategy_engine.indicators import rsi, bollinger

# 回测引擎核心逻辑（从 backtest_mode.py 提取简化版）
class SimpleBacktestEngine:
    def __init__(self, strategy, ohlcv_data, initial_capital, symbol, commission_rate=Decimal("0.001")):
        self.strategy = strategy
        self.ohlcv_data = ohlcv_data
        self.initial_capital = initial_capital
        self.symbol = symbol
        self.commission_rate = commission_rate
        
        self._cash = initial_capital
        self._position = Decimal("0")
        self._avg_entry_price = Decimal("0")
        self._trades = []
        self._equity_curve = []
        self._peak_equity = initial_capital

    async def run(self):
        self.strategy.on_start()
        for i, kline in enumerate(self.ohlcv_data):
            history = self.ohlcv_data[:i+1]
            self.strategy.ctx.set_price(self.symbol, Decimal(str(kline["close"])))
            
            # 运行策略逻辑
            signal = self.strategy.on_bar(kline, history)
            if signal:
                self._process_signal(signal, kline)
            
            # 记录权益
            equity = self._cash + self._position * Decimal(str(kline["close"]))
            self._peak_equity = max(self._peak_equity, equity)
            dd = float((equity - self._peak_equity) / self._peak_equity) if self._peak_equity > 0 else 0
            self._equity_curve.append({"timestamp": kline["timestamp"], "equity": float(equity), "drawdown": dd})
        
        self.strategy.on_stop()
        # 强制平仓
        if self._position > 0 and self.ohlcv_data:
            self._close_position(self.ohlcv_data[-1], "end_of_backtest")
            
        return self._compute_stats()

    def _process_signal(self, signal, kline):
        fill_price = Decimal(str(kline["close"]))
        if signal.side == "buy":
            cost = signal.quantity * fill_price
            commission = cost * self.commission_rate
            if cost + commission > self._cash:
                return # 资金不足
            self._cash -= (cost + commission)
            self._avg_entry_price = (self._avg_entry_price * self._position + cost) / (self._position + signal.quantity)
            self._position += signal.quantity
            self.strategy.ctx.balance = self._cash
            self.strategy.ctx.set_position(self.symbol, self._position)
        elif signal.side == "sell" and self._position > 0:
            sell_qty = min(signal.quantity, self._position)
            proceeds = sell_qty * fill_price
            commission = proceeds * self.commission_rate
            pnl = (fill_price - self._avg_entry_price) * sell_qty - commission
            self._cash += (proceeds - commission)
            self._position -= sell_qty
            self._trades.append({
                "entry_price": float(self._avg_entry_price), "exit_price": float(fill_price),
                "pnl": float(pnl), "signal": signal.signal_name
            })
            self.strategy.ctx.balance = self._cash
            self.strategy.ctx.set_position(self.symbol, self._position)

    def _close_position(self, kline, reason):
        fill_price = Decimal(str(kline["close"]))
        proceeds = self._position * fill_price
        commission = proceeds * self.commission_rate
        pnl = (fill_price - self._avg_entry_price) * self._position - commission
        self._cash += (proceeds - commission)
        self._trades.append({"entry_price": float(self._avg_entry_price), "exit_price": float(fill_price), "pnl": float(pnl), "signal": "close"})
        self._position = Decimal("0")

    def _compute_stats(self):
        final_equity = self._cash
        total_return = float(((final_equity - self.initial_capital) / self.initial_capital) * 100)
        closed = [t for t in self._trades if t.get("pnl") is not None]
        wins = [t for t in closed if t["pnl"] > 0]
        return {
            "final_equity": float(final_equity),
            "total_return_pct": total_return,
            "max_drawdown_pct": min(p["drawdown"] for p in self._equity_curve) * 100 if self._equity_curve else 0,
            "total_trades": len(closed),
            "win_rate": len(wins)/len(closed) if closed else 0,
            "trades": closed[-5:]
        }

def load_strategies():
    """手动加载策略文件"""
    path = os.path.expanduser("~/.tradelogx/strategies")
    strategies = {}
    for f in os.listdir(path):
        if f.endswith(".py") and not f.startswith("_"):
            mod_name = f[:-3]
            try:
                # 动态导入
                import importlib.util
                spec = importlib.util.spec_from_file_location(mod_name, os.path.join(path, f))
                mod = importlib.util.module_from_spec(spec)
                # 添加必要的别名，使策略中的 from apps... 能够工作
                sys.modules['apps.strategy_engine.base'] = __import__('apps.strategy_engine.base', fromlist=['BaseStrategy', 'StrategyContext'])
                sys.modules['apps.strategy_engine.indicators'] = __import__('apps.strategy_engine.indicators', fromlist=['rsi', 'bollinger'])
                spec.loader.exec_module(mod)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, type) and issubclass(attr, BaseStrategy) and attr is not BaseStrategy:
                        strategies[attr.name] = attr
            except Exception as e:
                print(f"加载策略 {f} 失败: {e}")
    return strategies

async def main():
    print("="*60)
    print("高频交易策略回测系统 (本地模式)")
    print("交易对: ETH/USDT | 周期: 5m | 初始资金: $10,000")
    print("="*60)

    # 读取数据
    data_path = os.path.expanduser("~/.tradelogx/data/eth_5m.json")
    print(f"\n[数据获取] 正在读取 {data_path}...")
    with open(data_path, "r") as f:
        ohlcv_data = json.load(f)
    print(f"[数据获取] 成功加载 {len(ohlcv_data)} 条 K 线数据")

    # 加载策略
    print("\n[策略加载] 扫描 ~/.tradelogx/strategies/ ...")
    all_strategies = load_strategies()
    target_strategies = ["mean_reversion", "momentum_breakout", "vwap_cross"]
    
    for s_name in target_strategies:
        if s_name not in all_strategies:
            print(f"[跳过] 策略 {s_name} 未找到")
            continue
            
        print(f"\n[回测中] 正在运行策略: {s_name}...")
        strategy_cls = all_strategies[s_name]
        ctx = StrategyContext(symbol="ETH/USDT", timeframe="5m", mode="backtest", params={"quantity": 0.1}, balance=Decimal("10000"))
        strategy = strategy_cls(ctx)
        
        engine = SimpleBacktestEngine(strategy, ohlcv_data, Decimal("10000"), "ETH/USDT")
        stats = await engine.run()
        
        print("\n" + "-"*60)
        print(f"策略: {s_name}")
        print(f"总收益率:   {stats['total_return_pct']:+.2f}%")
        print(f"最大回撤:   {stats['max_drawdown_pct']:.2f}%")
        print(f"总交易次数: {stats['total_trades']}")
        print(f"胜率:       {stats['win_rate']*100:.1f}%")

if __name__ == "__main__":
    asyncio.run(main())
