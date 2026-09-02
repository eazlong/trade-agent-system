# 关键 API 参考

> 由 SKILL.md 阶段 3.3 引用。生成策略代码时查阅本文获取准确的 API 签名与陷阱细节。

## StrategyContext（通过 self.ctx 访问）

| 方法/属性 | 类型 | 说明 |
|------|------|------|
| `self.ctx.symbol` | `str` | 当前交易品种（如 `"BTCUSDT"`） |
| `self.ctx.timeframe` | `str` | K 线周期（如 `"1h"`） |
| `self.ctx.mode` | `str` | 运行模式：`"backtest"` 或 `"live"` |
| `self.ctx.params` | `dict` | 策略参数字典 |
| `self.ctx.balance` | `Decimal` | 当前可用余额（未占用资金），≠ 总资金 |
| `self.ctx.position` | `Decimal` | 当前主标的持仓量 |
| `self.ctx.positions` | `dict[str, Decimal]` | 所有标的的持仓量 |
| `self.ctx.current_prices` | `dict[str, Decimal]` | 各标的当前价格 |
| `self.ctx.buy(quantity, signal_name)` | `OrderSignal` | 发买入信号 — **必须 return** |
| `self.ctx.sell(quantity, signal_name)` | `OrderSignal` | 发卖出信号 — **必须 return** |
| `self.ctx.close_position(signal_name)` | `OrderSignal` | 平仓信号 — **必须 return** |
| `self.ctx.to_portfolio_context()` | `PortfolioContext` | 获取多标的组合上下文（含 `total_capital`） |
| `self.ctx.set_position(symbol, value)` | `None` | 设置某标的持仓量 |
| `self.ctx.set_price(symbol, value)` | `None` | 设置某标的当前价格 |

> ⚠️ 信号方法**必须作为 `on_bar` 的返回值返回**。详见 SKILL.md 生成代码必查清单 #2。

## BaseStrategy 便捷方法

| 方法 | 说明 |
|------|------|
| `self.history(symbol?, timeframe?, n?)` | 获取历史 K 线（仅实盘用） |
| `self.last_bar(symbol?, timeframe?)` | 获取最后一根 K 线 |
| `self.higher_tf()` | 获取更高周期 DataFeed |
| `self.lower_tf()` | 获取更低周期 DataFeed |
| `self.get_watch_signals() -> list[dict]` | 返回最小周期信号配置，部署实盘时自动注册到 SignalMonitor（见 references/watch-signals.md） |

## 仓位计算

**核心概念**：`总资金 = balance + 持仓市值`。`balance` 是可用现金，不是总资金。

**两种模式**（详见 SKILL.md 清单 #5）：
1. **手动计算**（默认）：不设 `self.portfolio`，在 `on_bar` 中计算 quantity，直接生效
2. **PctCapitalPortfolio**：设 `self.portfolio = PctCapitalPortfolio()`，`on_bar` 的 quantity 被覆盖，由 Portfolio 自动计算

**⚠️ 二选一，不能混用** — 否则手动计算的值会被静默覆盖。

### 方法 1：手动计算（推荐）

```python
def on_bar(self, kline, history):
    if self.ctx.position == 0 and buy_condition:
        total_capital = self.ctx.to_portfolio_context().total_capital
        price = Decimal(str(kline["close"]))
        position_pct = Decimal(str(self.ctx.params.get("position_pct", 0.1)))
        qty = (total_capital * position_pct / price).quantize(Decimal("0.0001"))
        return self.ctx.buy(quantity=qty, signal_name="entry")
```

### 方法 2：PctCapitalPortfolio

```python
from apps.strategy_engine.portfolio import PctCapitalPortfolio

@register_strategy("{strategy_name}")
class MyStrategy(BaseStrategy):
    def __init__(self, context: StrategyContext):
        super().__init__(context)
        self.portfolio = PctCapitalPortfolio()  # 设后 on_bar 的 quantity 被覆盖

    def on_bar(self, kline, history):
        if self.ctx.position == 0 and buy_condition:
            return self.ctx.buy(quantity=Decimal("1"), signal_name="entry")  # 占位值
```

### 方法 3：固定数量

```python
def on_bar(self, kline, history):
    if self.ctx.position == 0 and buy_condition:
        qty = Decimal(str(self.ctx.params.get("quantity", 0.01)))
        return self.ctx.buy(quantity=qty, signal_name="entry")
```

## 路径依赖指标的回溯窗口

**概念**：路径依赖型指标（EMA、ATR）的当前值依赖历史所有数据。对全部 `history` 计算时，不同回测起始日期会产生不同值，导致同一日历日期信号不一致（"2年回测比1年交易少"的根因）。

**规则**：使用 `history[-period*5:]` 限制回溯窗口（详见 SKILL.md 清单 #4）。

**为什么 `period * 5`？** EMA 指数衰减权重：`(1 - 2/(period+1))^n`。当 `n = period*5` 时，初始种子权重 ≈ 0.005%，可忽略。

**示例**：
```python
# ✅ 路径依赖：EMA/ATR 用有限窗口
_lookback = min(self.ema_period * 5, len(history))
ema_array = ema(history[-_lookback:], period=self.ema_period)

# ✅ 非路径依赖：SMA/RSI/Bollinger/Donchian 可用全部 history
rsi_array = rsi(history, period=14)
```

## 可用技术指标（来自 `indicators`）

> **重要**：以下所有指标函数均返回 `np.ndarray`，必须用 `float(arr[-1])` 取最新值后才能比较。

- `sma(history, period=20) -> np.ndarray` — 简单移动平均
- `ema(history, period=12) -> np.ndarray` — 指数移动平均
- `rsi(history, period=14) -> np.ndarray` — 相对强弱指数
- `macd(history, fast=12, slow=26, signal_period=9) -> dict[str, np.ndarray]` → `{"macd", "signal", "histogram"}`
- `bollinger(history, period=20, std_dev=2.0) -> dict[str, np.ndarray]` → `{"upper", "middle", "lower"}`
- `atr(history, period=14) -> np.ndarray` — 平均真实波幅，history 为 K 线 dict 列表；也可用 `atr(highs, lows, closes, period=N)` 直接传 np.ndarray 数组
- `stoch(history, k_period=14, d_period=3) -> dict[str, np.ndarray]` → `{"k", "d"}`

## 指标返回值处理

所有指标返回 `np.ndarray`，必须：
1. `len(arr) == 0` 检查空数组
2. `float(arr[-1])` 取最新值转 float
3. 之后才能安全比较

**详见 SKILL.md 清单 #6。**

## OrderSignal 构造

```python
# 买入
self.ctx.buy(quantity=Decimal("0.01"), signal_name="rsi_cross")

# 卖出
self.ctx.sell(quantity=Decimal("0.01"), signal_name="take_profit")

# 平仓（卖出全部持仓）
self.ctx.close_position()
```

信号命名规范：`entry`、`exit`、`stop_loss`、`take_profit` 等。
