---
name: quant-backtest
description: 使用 FetchOHLCVTool 获取 K 线数据，基于 VectorBT 框架执行策略回测，结果保存到 BacktestResult 模型
version: 1.0.0
---

# 量化策略回测技能

使用 VectorBT 进行加密货币策略回测，从数据获取到结果入库的完整流程。

## 适用场景

- "帮我回测这个策略"
- "用 RSI + EMA 策略跑一下 BTC 日线回测"
- "这个策略在过去一年的表现如何"
- "参数优化：找出 RSI 最佳周期"

## 回测流程

### 步骤 1：确认策略参数

收集以下回测配置：

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| symbol | 否 | BTC/USDT | 交易品种 |
| timeframe | 否 | 1d | K 线周期（1m/5m/15m/30m/1h/4h/1d） |
| exchange | 否 | binance | 交易所 |
| start_date | 是 | — | 回测开始日期（如 2024-01-01） |
| end_date | 是 | — | 回测结束日期（如 2024-12-31） |
| initial_capital | 否 | 10000 | 初始资金（USDT） |
| commission_rate | 否 | 0.001 | 手续费率（0.1%） |
| strategy_type | 是 | — | 策略类型（rsi_cross, ema_cross, macd_cross, bollinger 等） |
| parameters | 否 | {} | 策略参数字典 |

如果用户未提供完整信息，先询问确认。

### 步骤 2：获取 K 线数据

调用 `fetch_ohlcv` 工具获取历史数据：

```python
# 工具调用示例（由 LLM 执行）
# fetch_ohlcv(symbol="BTC/USDT", timeframe="1d", limit=500, exchange="binance")
```

工具返回 `temp_file` 路径，包含 OHLCV 数据的 JSON 文件。

**注意**：
- 回测需要足够的历史数据，`limit` 至少覆盖回测区间
- 日线回测一年约需 365 根 K 线，建议设置 500 上限
- 小时线回测需要更多数据点

### 步骤 3：编写回测脚本

使用 Python + VectorBT 编写回测脚本。以下是常用策略模板：

#### 3.1 EMA 交叉策略

```python
import vectorbt as vbt
import pandas as pd
import json
from pathlib import Path
from datetime import date

def run_ema_cross(temp_file: str, params: dict, config: dict) -> dict:
    """EMA 交叉回测"""
    records = json.loads(Path(temp_file).read_text())
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    fast = params.get('fast', 10)
    slow = params.get('slow', 30)

    fast_ma = vbt.MA.run(df['close'], window=fast, short_name='fast')
    slow_ma = vbt.MA.run(df['close'], window=slow, short_name='slow')

    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = slow_ma.ma_crossed_above(fast_ma)

    portfolio = vbt.Portfolio.from_signals(
        df['close'],
        entries.values,
        exits.values,
        init_cash=config.get('initial_capital', 10000),
        fees=config.get('commission_rate', 0.001),
        slippage=0.0001,
    )

    return compute_metrics(portfolio, df, config)
```

#### 3.2 RSI 超买超卖策略

```python
def run_rsi_cross(temp_file: str, params: dict, config: dict) -> dict:
    """RSI 超买超卖回测"""
    records = json.loads(Path(temp_file).read_text())
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    rsi_period = params.get('rsi_period', 14)
    oversold = params.get('oversold', 30)
    overbought = params.get('overbought', 70)

    rsi = vbt.RSI.run(df['close'], window=rsi_period)

    entries = rsi.rsi_crossed_below(oversold)
    exits = rsi.rsi_crossed_above(overbought)

    portfolio = vbt.Portfolio.from_signals(
        df['close'],
        entries.values,
        exits.values,
        init_cash=config.get('initial_capital', 10000),
        fees=config.get('commission_rate', 0.001),
        slippage=0.0001,
    )

    return compute_metrics(portfolio, df, config)
```

#### 3.3 MACD 策略

```python
def run_macd_cross(temp_file: str, params: dict, config: dict) -> dict:
    """MACD 交叉回测"""
    records = json.loads(Path(temp_file).read_text())
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    fast = params.get('fast', 12)
    slow = params.get('slow', 26)
    signal = params.get('signal', 9)

    macd = vbt.MACD.run(df['close'], fast_period=fast, slow_period=slow, signal_period=signal)

    entries = macd.macd_crossed_above(macd.signal)
    exits = macd.macd_crossed_below(macd.signal)

    portfolio = vbt.Portfolio.from_signals(
        df['close'],
        entries.values,
        exits.values,
        init_cash=config.get('initial_capital', 10000),
        fees=config.get('commission_rate', 0.001),
        slippage=0.0001,
    )

    return compute_metrics(portfolio, df, config)
```

#### 3.4 布林带策略

```python
def run_bollinger(temp_file: str, params: dict, config: dict) -> dict:
    """布林带回归回测"""
    records = json.loads(Path(temp_file).read_text())
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    window = params.get('window', 20)
    std_dev = params.get('std_dev', 2.0)

    bbands = vbt.BBANDS.run(df['close'], window=window, alpha=std_dev)

    # 价格触及下轨买入，触及上轨卖出
    entries = df['close'].values < bbands.lower.values
    exits = df['close'].values > bbands.upper.values

    portfolio = vbt.Portfolio.from_signals(
        df['close'],
        entries,
        exits,
        init_cash=config.get('initial_capital', 10000),
        fees=config.get('commission_rate', 0.001),
        slippage=0.0001,
    )

    return compute_metrics(portfolio, df, config)
```

#### 3.5 通用指标计算函数

所有策略共享的指标计算逻辑：

```python
def compute_metrics(portfolio, df: pd.DataFrame, config: dict) -> dict:
    """计算回测绩效指标"""
    total_return = portfolio.total_return()
    sharpe = portfolio.sharpe_ratio()
    sortino = portfolio.sortino_ratio()
    max_drawdown = portfolio.max_drawdown()

    # 胜率
    trades = portfolio.trades
    win_rate = trades.won().mean() if len(trades) > 0 else 0.0

    # 年化收益率
    days = (df.index[-1] - df.index[0]).days
    annualized = (1 + total_return) ** (365 / max(days, 1)) - 1

    return {
        'total_return_pct': float(total_return) * 100,
        'annualized_return_pct': float(annualized) * 100,
        'sharpe_ratio': float(sharpe) if not pd.isna(sharpe) else None,
        'sortino_ratio': float(sortino) if not pd.isna(sortino) else None,
        'max_drawdown_pct': float(max_drawdown) * 100 if not pd.isna(max_drawdown) else None,
        'win_rate': float(win_rate) if not pd.isna(win_rate) else None,
        'total_trades': int(len(trades)),
        'profit_factor': float(profit_factor) if not pd.isna(profit_factor := portfolio.trades.profit_factor()) else None,
        'avg_return_per_trade': float(portfolio.trades.return_mean()) if len(trades) > 0 else 0.0,
    }
```

### 步骤 4：参数优化（可选）

如果用户要求参数优化，使用 VectorBT 的广播机制进行网格搜索：

```python
def param_optimize_ema(temp_file: str, config: dict) -> dict:
    """EMA 交叉参数优化"""
    records = json.loads(Path(temp_file).read_text())
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    # 广播多个参数组合
    fast_windows = range(5, 30, 5)
    slow_windows = range(30, 100, 10)

    fast_ma = vbt.MA.run(df['close'], window=fast_windows, short_name='fast')
    slow_ma = vbt.MA.run(df['close'], window=slow_windows, short_name='slow')

    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = slow_ma.ma_crossed_above(fast_ma)

    portfolio = vbt.Portfolio.from_signals(
        df['close'],
        entries.values,
        exits.values,
        init_cash=config.get('initial_capital', 10000),
        fees=config.get('commission_rate', 0.001),
    )

    # 找出 Sharpe 最高的参数组合
    sharpe_matrix = portfolio.sharpe_ratio()
    best_idx = sharpe_matrix.values.argmax()
    best_fast = fast_windows[best_idx[0]]
    best_slow = slow_windows[best_idx[1]]

    return {
        'best_params': {'fast': int(best_fast), 'slow': int(best_slow)},
        'best_sharpe': float(sharpe_matrix.values.flat[best_idx]),
    }
```

### 步骤 5：提取中间结果（权益曲线 + 交易日志）

从 VectorBT 提取时间序列数据，用于前端图表展示：

```python
def extract_curve_and_trades(portfolio, df: pd.DataFrame) -> dict:
    """提取权益曲线和交易日志"""
    import pandas as pd

    # 权益曲线
    equity = portfolio.value()
    drawdown = portfolio.drawdowns().drawdown

    equity_curve = []
    for i in range(len(equity)):
        ts = df.index[i] if hasattr(df.index[i], 'isoformat') else str(df.index[i])
        equity_curve.append({
            'timestamp': ts if isinstance(ts, str) else ts.isoformat(),
            'equity': float(equity.iloc[i]),
            'drawdown': float(drawdown.iloc[i]) if i < len(drawdown) else 0.0,
        })

    # 回撤曲线（独立存储，便于单独获取）
    drawdown_curve = []
    for i in range(len(drawdown)):
        ts = df.index[i] if hasattr(df.index[i], 'isoformat') else str(df.index[i])
        drawdown_curve.append({
            'timestamp': ts if isinstance(ts, str) else ts.isoformat(),
            'drawdown': float(drawdown.iloc[i]),
        })

    # 交易日志
    trades = portfolio.trades.records_readable
    trade_list = []
    cum_pnl = 0.0
    for _, t in trades.iterrows():
        cum_pnl += float(t.get('PnL', 0))
        trade_list.append({
            'entry_time': t.get('Entry Index', df.index[0]),
            'exit_time': t.get('Exit Index', df.index[-1]),
            'side': 'long' if t.get('Side', 0) == 1 else 'short',
            'entry_price': float(t.get('Entry Price', 0)),
            'exit_price': float(t.get('Exit Price', 0)),
            'quantity': float(t.get('Size', 0)),
            'pnl': float(t.get('PnL', 0)),
            'tags': [],
        })

    return {
        'equity_curve': equity_curve,
        'drawdown_curve': drawdown_curve,
        'trades': trade_list,
    }
```

### 步骤 6：保存回测结果

将回测结果（含中间数据）保存到数据库：

```python
import os
import django
import subprocess
from datetime import date
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')
django.setup()

from apps.backtest.models import BacktestResult, BacktestTrade
from apps.trading.models import Strategy


def downsample(points: list, max_points: int = 2000) -> list:
    """简单等间隔降采样"""
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    return points[::step]


def save_backtest_result(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    initial_capital: float,
    metrics: dict,
    parameters: dict,
    curve_data: dict | None = None,
    code_path: str = '',
) -> BacktestResult:
    """保存回测结果到数据库（含权益曲线和交易日志）"""
    # 获取或创建 Strategy
    strategy, _ = Strategy.objects.get_or_create(
        name=strategy_name,
        defaults={'code_path': code_path or f'strategies/{strategy_name.lower().replace(" ", "_")}.py'},
    )

    # 获取当前 git commit hash
    try:
        git_hash = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd='backend',
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        git_hash = ''

    # 降采样曲线数据
    equity_curve = downsample(curve_data['equity_curve']) if curve_data else []
    drawdown_curve = downsample(curve_data['drawdown_curve']) if curve_data else []

    result = BacktestResult.objects.create(
        strategy=strategy,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=Decimal(str(initial_capital)),
        final_capital=Decimal(str(initial_capital * (1 + metrics['total_return_pct'] / 100))),
        total_return_pct=metrics['total_return_pct'],
        sharpe_ratio=metrics.get('sharpe_ratio'),
        max_drawdown_pct=metrics.get('max_drawdown_pct'),
        win_rate=metrics.get('win_rate'),
        total_trades=metrics.get('total_trades', 0),
        git_commit_hash=git_hash,
        parameters=parameters,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
    )

    # 批量写入交易日志
    if curve_data and curve_data.get('trades'):
        trade_objs = []
        for t in curve_data['trades']:
            entry_time = t['entry_time']
            if hasattr(entry_time, 'to_pydatetime'):
                entry_time = entry_time.to_pydatetime()
            if not hasattr(entry_time, 'isoformat'):
                from datetime import datetime
                entry_time = datetime.fromisoformat(str(entry_time))

            exit_time = t.get('exit_time')
            if exit_time and hasattr(exit_time, 'to_pydatetime'):
                exit_time = exit_time.to_pydatetime()
            if exit_time and not hasattr(exit_time, 'isoformat'):
                from datetime import datetime
                exit_time = datetime.fromisoformat(str(exit_time))

            trade_objs.append(BacktestTrade(
                backtest=result,
                entry_time=entry_time,
                exit_time=exit_time,
                symbol=symbol,
                side=t['side'],
                entry_price=Decimal(str(t['entry_price'])),
                exit_price=Decimal(str(t['exit_price'])) if t.get('exit_price') else None,
                quantity=Decimal(str(t['quantity'])),
                pnl=Decimal(str(t['pnl'])),
                fees=Decimal('0'),
                tags=t.get('tags', []),
            ))
        BacktestTrade.objects.bulk_create(trade_objs, batch_size=500)

    return result
```

### 步骤 7：输出回测报告

将回测结果格式化为可读报告：

```
## 回测报告

**策略**: {strategy_name}
**品种**: {symbol}
**周期**: {timeframe}
**区间**: {start_date} ~ {end_date}
**初始资金**: {initial_capital} USDT

### 绩效指标

| 指标 | 数值 |
|------|------|
| 总收益率 | {total_return_pct}% |
| 年化收益率 | {annualized_return_pct}% |
| 夏普比率 | {sharpe_ratio} |
| 索提诺比率 | {sortino_ratio} |
| 最大回撤 | {max_drawdown_pct}% |
| 胜率 | {win_rate}% |
| 总交易次数 | {total_trades} |
| 盈亏比 | {profit_factor} |

### 策略参数

{parameters JSON}

### 分析与建议

[基于绩效指标的自然语言分析]
```

## 完整执行示例

用户请求："用 EMA 交叉策略回测 BTC/USDT 日线，2024 年全年，快线 10，慢线 30"

执行步骤：

1. 调用 `fetch_ohlcv(symbol="BTC/USDT", timeframe="1d", limit=500, exchange="binance")`
2. 读取 temp_file 中的 OHLCV 数据
3. 编写并执行 EMA 交叉回测脚本
4. 提取权益曲线和交易日志
5. 调用 `save_backtest_result` 写入数据库（含曲线降采样和交易批量写入）
6. 输出回测报告
7. 前端可通过 `/backtest/{id}` 查看权益曲线、回撤曲线和交易明细

## 前端展示

回测结果可通过前端页面查看：

- **回测列表**：`/backtest` — 展示所有历史回测的摘要表格
- **回测详情**：`/backtest/{id}` — 权益曲线、回撤曲线（Tab 切换）、分页交易表格
- **API**：
  - `GET /api/backtest/results/<id>/detail/` — 返回摘要 + equity_curve + drawdown_curve
  - `GET /api/backtest/results/<id>/trades/?page=1&page_size=50` — 分页交易日志

## 注意事项

- **数据质量**：回测前检查 K 线数据完整性，确保无大面积缺口
- **手续费**：加密货币默认手续费 0.1%，VectorBT 中通过 `fees` 参数设置
- **滑点**：建议设置 0.01% 滑点使回测更贴近实际
- **过拟合**：参数优化后务必做样本外验证，避免过拟合
- **向量化限制**：VectorBT 是向量化引擎，不支持复杂的事件驱动策略（如动态仓位调整），此类场景建议用事件驱动框架
- **模型字段**：`BacktestResult` 的 `strategy` 是外键，关联 `trading.Strategy` 表；`initial_capital` 和 `final_capital` 使用 `DecimalField`，计算时需转换为 `Decimal`
- **降采样**：权益曲线 > 2000 点时自动等间隔降采样，写入 DB 前完成
- **交易批量写入**：使用 `bulk_create(batch_size=500)` 高效写入交易日志
- **依赖**：确保环境已安装 `vectorbt`（`pip install vectorbt`）和 `pandas-ta`
