---
name: create-strategy
description: 创建量化交易策略 — 通过对话了解需求，生成继承自 BaseStrategy 的策略代码文件
when_to_use: 用户要求"创建策略"、"编写策略"、"新建一个交易策略"时激活
---

# 创建策略技能

通过结构化对话理解用户需求，生成继承自 `BaseStrategy` 的量化交易策略。

## 核心原则

1. **先对话，后编码** — 必须通过提问确认需求，不能直接生成代码
2. **确认后再生成** — 需求不完整时持续追问，完整后给出总结并确认
3. **自动生成策略名** — 根据策略逻辑自动生成 `{indicator1}_{indicator2}_strategy` 格式的名称，无需用户确认
4. **输出到统一策略目录** — 生成到 `~/.tradelogx/strategies/` 目录

## 策略命名规则

**格式**：`{核心指标1}_{核心指标2}_strategy`

由 agent 根据策略使用的指标自动生成，示例：

| 策略逻辑 | strategy_name |
|---------|---------------|
| RSI 超买超卖 + EMA 均线交叉 | `rsi_ema_strategy` |
| 布林带 + RSI 超卖 | `bollinger_rsi_strategy` |
| MACD 交叉 + ATR 止损 | `macd_atr_strategy` |
| 双均线交叉 | `ma_cross_strategy` |
| 仅 RSI 超买超卖 | `rsi_strategy` |
| 仅布林带回归 | `bollinger_strategy` |

**命名优先级**：
1. 取策略中最重要的 1-2 个指标， `_` 连接
2. 辅以策略类型（如 `breakout`、`regression`、`cross`）
3. 最后加 `_strategy` 后缀
4. 全部小写，英文

## 工作流程

### 阶段 1：需求收集（对话轮次）

通过提问收集以下信息，每轮聚焦 1-2 个维度：

#### 1.1 策略基本信息
- 核心策略逻辑（一句话描述）→ agent 自动推导 strategy_name
- 交易品种（单品种如 BTCUSDT，或多品种）
- 交易周期（如 1h、4h、1d）

#### 1.2 交易逻辑
- **入场条件**：什么情况下买入？（指标条件、价格条件等）
- **出场条件**：什么情况下卖出？（止盈/止损/信号反转）
- **仓位管理**：每次开多少仓位？（固定数量/百分比/动态计算）
- **是否对冲/做空**：仅做多还是多空都做？

#### 1.3 技术指标
- 使用哪些指标？（如 RSI、MACD、MA、布林带、ATR 等）
- 指标参数？（周期、阈值等）

#### 1.4 风控规则
- 止损比例？
- 止盈比例？
- 单日最大亏损限制？

### 阶段 2：确认需求

在生成代码前，总结用户需求并明确确认：

```
我理解你的策略需求如下：

- **策略名称**：`xxx_strategy`（自动生成）
- **交易品种**：`BTCUSDT`（或 `BTCUSDT, ETHUSDT`）
- **交易周期**：`1h`
- **入场逻辑**：...
- **出场逻辑**：...
- **风控**：...
- **参数**：...

请确认是否正确，我将开始生成策略代码。
```

**必须等待用户确认后才能生成代码。**

### 阶段 3：生成策略代码

确认后，按以下步骤生成策略 Python 文件：

#### 3.1 分析入场条件

从用户确认的需求中提取：
- **主要指标**：如 RSI、MACD、布林带、EMA 等
- **触发条件**：如"RSI < 30"、"价格突破布林带上轨"、"EMA 上穿 EMA_slow"
- **K线周期**：如 1h、4h、15m

#### 3.2 推导 watch signals

根据推导规则（见下方"Watch Signals 推导规则"章节），将入场条件映射为最小周期信号：

1. **简化条件** — 复合条件拆解为单指标条件（如"EMA向上 且 RSI<30" → 只取"RSI<30"）
2. **选择周期** — 使用策略的最小K线周期作为 interval
3. **映射格式** — 转换为标准 watch signal 格式：
   ```python
   {
       "interval": "1h",
       "indicator_type": "rsi",
       "indicator_params": {"period": 14},
       "condition": {"operator": "lt", "left": {"field": "rsi"}, "right": {"value": 30}},
       "trigger_type": "continuous"
   }
   ```

#### 3.3 生成完整代码

生成包含以下部分的策略代码：
- `on_bar` 实现
- `get_watch_signals` 实现（基于 3.2 推导结果）
- `on_start`、`on_stop` 等生命周期方法

**输出路径**：`~/.tradelogx/strategies/{strategy_name}.py`

调用 `write_file` 工具：
```
write_file(
    file_path="~/.tradelogx/strategies/{strategy_name}.py",
    agent_name="quant",
    content="<策略代码>"
)
```

策略创建成功后，告知用户：
- strategy_name（供回测使用）
- 文件路径

## Watch Signals 推导规则

Agent 在阶段3生成代码时，需要将策略入场条件推导为 watch signals。以下是推导规则：

### 1. 简化原则

**只保留单指标条件**，复合条件在 `on_bar()` 中完整验证：

| 入场条件 | watch signal | on_bar 验证 |
|---------|-------------|------------|
| RSI < 30 且 EMA向上 | RSI < 30 | RSI < 30 && EMA_slope > 0 |
| 价格突破布林带上轨 且 MACD金叉 | 价格突破布林带上轨 | price > bb_upper && macd > signal |
| EMA_fast 上穿 EMA_slow | EMA_fast 上穿 EMA_slow | 完整条件（单指标已足够） |

### 2. 指标类型映射

将策略中使用的指标映射为 `indicator_type`：

| 策略指标 | indicator_type | indicator_params |
|---------|---------------|-----------------|
| RSI | `"rsi"` | `{"period": 14}` |
| EMA | `"ema"` | `{"period": 20}` |
| 布林带 | `"bollinger"` | `{"period": 20, "std_dev": 2.0}` |
| MACD | `"macd"` | `{"fast": 12, "slow": 26, "signal_period": 9}` |
| 唐奇安通道 | `"donchian"` | `{"period": 20}` |
| ATR | `"atr"` | `{"period": 14}` |

### 3. 条件运算符映射

将入场条件转换为 `condition` 格式：

| 条件描述 | operator | left | right |
|---------|---------|------|-------|
| RSI < 30 | `"lt"` | `{"field": "rsi"}` | `{"value": 30}` |
| RSI > 70 | `"gt"` | `{"field": "rsi"}` | `{"value": 70}` |
| 价格上穿布林带上轨 | `"cross_above"` | `{"field": "price"}` | `{"field": "upper"}` |
| EMA 下穿 EMA_slow | `"cross_below"` | `{"field": "ema"}` | `{"field": "ema_slow"}` |
| 价格 > 50000 | `"gt"` | `{"field": "price"}` | `{"value": 50000}` |

### 4. 周期选择规则

使用策略的**最小K线周期**作为 `interval`：

- 策略使用 4h + 1d → watch signal 使用 `"4h"`
- 策略仅使用 1h → watch signal 使用 `"1h"`
- 多时间框架策略 → 使用最小周期（如 15m + 1h → `"15m"`）

### 5. trigger_type 选择

- **持续信号**（如 RSI < 30）→ `"continuous"`
- **突破信号**（如 价格上穿布林带）→ `"once"`（首次触发后移除）

### 6. 多条件策略示例

**策略入场条件**：4h EMA向上 且 15m 价格突破唐奇安通道上轨

**推导过程**：
1. 简化：只保留最小周期条件"15m 价格突破唐奇安上轨"
2. 映射：
   - indicator_type: `"donchian"`
   - indicator_params: `{"period": 20}`
   - condition: `{"operator": "cross_above", "left": {"field": "price"}, "right": {"field": "upper"}}`
3. 周期：`"15m"`（最小周期）
4. trigger_type: `"continuous"`

**生成的 watch signal**：
```python
def get_watch_signals(self) -> list[dict]:
    return [
        {
            "interval": "15m",
            "indicator_type": "donchian",
            "indicator_params": {"period": 20},
            "condition": {
                "operator": "cross_above",
                "left": {"field": "price"},
                "right": {"field": "upper"},
            },
            "trigger_type": "continuous",
        }
    ]
```

**`on_bar()` 中完整验证**：
```python
# 4h EMA 向上（在 on_bar 中验证）
ema_4h = ema(higher_tf_history, period=20)
ema_slope = ema_4h[-1] - ema_4h[-2]

# 15m 价格突破唐奇安（watch signal 已触发）
donchian_result = donchian(history, period=20)
upper = float(donchian_result["upper"][-1])

if ema_slope > 0 and kline["close"] > upper:
    return self.ctx.buy(quantity=qty, signal_name="entry")
```

### 阶段 4：测试策略

策略代码写入后，**必须**调用 `test_strategy` 工具验证策略能正确运行：

```
test_strategy(
    strategy_name="{strategy_name}",
    strategy_file_path="strategies/{strategy_name}.py"
)
```

如果测试失败（返回 data 中 `success: false`），根据错误信息修正代码：
1. 分析错误类型（语法错误 / 加载失败 / 运行时错误）
2. 修改策略代码文件
3. 重新调用 `test_strategy` 测试
4. 重复直到测试通过

测试通过后，告知用户：
- strategy_name（供回测使用）
- 文件路径
- 模拟运行统计（信号数、买卖次数、模拟收益率）

## 代码质量要求

### on_bar 签名（最关键）

> `test_strategy` 工具会自动检测策略代码的问题，包括语法错误、加载失败、运行时异常等。

**必须严格遵守以下签名，禁止任何变体：**

```python
def on_bar(self, kline: dict, history: list[dict]) -> OrderSignal | None:
```

- `kline`：当前 K 线 {open, high, low, close, volume, timestamp}
- `history`：历史 K 线列表（含当前 K 线），由回测引擎传入，**不是 StrategyContext**
- **禁止在 on_bar 中调用 `ctx.history()`** — history 直接作为参数传入
- `ctx`（StrategyContext）通过 `self.ctx` 访问

### 两个常见陷阱（CRITICAL — 90% 的零交易 bug 由此引起）

#### 陷阱 1：调用了 `ctx.buy()` 但没有 `return`

**错误写法（产生 0 交易）：**
```python
def on_bar(self, kline, history):
    if should_buy:
        self.ctx.buy(quantity=qty, signal_name="entry")  # ❌ 生成信号但丢弃了
        return None  # ❌ 引擎收到 None，认为无信号
```

**正确写法：**
```python
def on_bar(self, kline, history):
    if should_buy:
        return self.ctx.buy(quantity=qty, signal_name="entry")  # ✅ 返回信号
    return None
```

**规则**：`self.ctx.buy()` / `self.ctx.sell()` / `self.ctx.close_position()` **必须作为返回值返回**，不能仅调用而不 return。

#### 陷阱 2：缺少 `@register_strategy()` 装饰器

**错误写法：**
```python
from apps.strategy_engine.base import BaseStrategy

class MyStrategy(BaseStrategy):  # ❌ 未注册，回测引擎找不到
    name = "my_strategy"
```

**正确写法：**
```python
from apps.strategy_engine.base import BaseStrategy
from apps.strategy_engine.registry import register_strategy

@register_strategy()
class MyStrategy(BaseStrategy):  # ✅ 已注册
    name = "my_strategy"
```

### 文件模板结构

```python
"""
{策略名称} - {一句话描述}

策略逻辑：
- {入场条件}
- {出场条件}

风控：{风控规则}
"""

from decimal import Decimal

from apps.strategy_engine.base import BaseStrategy, StrategyContext
from apps.strategy_engine.indicators import rsi, sma, ema, macd, bollinger, atr, stoch
from apps.strategy_engine.registry import register_strategy


@register_strategy()
class {StrategyName}Strategy(BaseStrategy):
    """{策略描述}"""

    name = "{strategy_name}"
    description = "{一句话描述}"
    params_schema = {
        # 可调参数
        # "param_name": {"type": "number"/"integer"/"string", "default": value},
    }

    def __init__(self, context: StrategyContext):
        super().__init__(context)
        self.bb_period = context.params.get("bb_period", 20)
        self.min_bars = self.bb_period + 5

    def on_bar(self, kline: dict, history: list[dict]):
        """
        每根 K 线完成时调用。

        Args:
            kline: 当前 K 线 {open, high, low, close, volume, timestamp}
            history: 历史 K 线列表（含当前 K 线）

        Returns:
            OrderSignal 或 None
        """
        if len(history) < self.min_bars:
            return None

        # 计算指标
        # closes = [bar["close"] for bar in history]

        # 访问上下文用 self.ctx
        # self.ctx.position  # 当前持仓
        # self.ctx.balance   # 当前余额

        # ⚠️ 必须 return 信号，不能仅调用！
        # 买入前检查：self.ctx.position == 0
        # 卖出前检查：self.ctx.position > 0

        # ✅ 正确：return self.ctx.buy(quantity=..., signal_name="entry")
        # ✅ 正确：return self.ctx.sell(quantity=..., signal_name="exit")
        # ✅ 正确：return self.ctx.close_position(signal_name="take_profit")

        return None

    def on_start(self) -> None:
        """策略启动时调用（初始化指标状态等）"""

    def on_stop(self) -> None:
        """策略停止时调用"""

    def get_watch_signals(self) -> list[dict]:
        """返回最小周期信号配置，由 LiveStrategyRunner 注册到 SignalMonitor 做初筛。

        **重要**：此方法由 agent 在阶段3根据策略入场条件自动推导生成，
        不需要用户手动配置。推导规则见上方"Watch Signals 推导规则"章节。

        示例（RSI超卖策略）：
        return [
            {
                "interval": "1h",
                "indicator_type": "rsi",
                "indicator_params": {"period": 14},
                "condition": {"operator": "lt", "left": {"field": "rsi"}, "right": {"value": 30}},
                "trigger_type": "continuous",
            }
        ]

        每个 dict 包含:
            interval: str         — K线周期 (如 "15m", "1h")
            indicator_type: str   — 指标类型 (donchian/bollinger/rsi/price_watch/...)
            indicator_params: dict — 指标参数 (如 {"period": 20})
            condition: dict       — 触发条件，格式见下方说明
            trigger_type: str     — "once"(单次) 或 "continuous"(持续)

        默认返回 []。子类覆盖此方法启用两阶段信号检测：
        1. SignalMonitor 按最小周期检测单指标条件（轻量初筛）
        2. 触发后运行完整 on_bar() 验证多指标组合条件

        条件格式示例:
            # 价格上穿唐奇安通道上轨
            {"operator": "cross_above", "left": {"field": "price"},
             "right": {"field": "upper"}}
            # 价格大于指定值
            {"operator": "gt", "left": {"field": "price"},
             "right": {"value": 50000}}
            # RSI 小于阈值
            {"operator": "lt", "left": {"field": "rsi"},
             "right": {"value": 30}}
        """
        return []  # agent 将根据推导规则填充实际内容
```

### 关键 API 参考

#### StrategyContext（通过 self.ctx 访问）

| 方法/属性 | 说明 |
|------|------|
| `self.ctx.position` | 当前持仓量 |
| `self.ctx.balance` | 当前余额 |
| `self.ctx.buy(quantity, signal_name)` | 发买入信号 — **必须 return** |
| `self.ctx.sell(quantity, signal_name)` | 发卖出信号 — **必须 return** |
| `self.ctx.close_position(signal_name)` | 平仓信号 — **必须 return** |
| `self.ctx.params` | 策略参数字典 |

> ⚠️ 以上所有发信号的方法**必须作为 `on_bar` 的返回值返回**，不能仅调用而不 return。
> 引擎通过 `on_bar` 的返回值判断是否有信号，调用但不 return = 0 交易。

#### BaseStrategy 便捷方法

| 方法 | 说明 |
|------|------|
| `self.history(symbol?, timeframe?, n?)` | 获取历史 K 线（仅实盘用） |
| `self.last_bar(symbol?, timeframe?)` | 获取最后一根 K 线 |
| `self.higher_tf()` | 获取更高周期 DataFeed |
| `self.lower_tf()` | 获取更低周期 DataFeed |
| `self.get_watch_signals() -> list[dict]` | 返回最小周期信号配置，部署实盘时自动注册到 SignalMonitor |

#### get_watch_signals — 实盘信号预筛选（可选覆盖）

当策略部署到实盘/测试网时，`LiveStrategyRunner` 会调用此方法将最小周期信号注册到 `SignalMonitor`，实现两阶段信号检测：

```
SignalMonitor 定时检查单指标条件（轻量初筛）
  → 触发 → Redis List 事件 → LiveStrategyRunner 消费
  → 运行完整 on_bar() 验证多指标组合 → dispatch 交易信号
```

**典型场景**：策略需要"4h EMA 趋势向上 + 15m 价格突破 Donchian 上轨"，则只需将 15m Donchian 突破注册为 watch signal，4h EMA 趋势在 `on_bar()` 中完整验证。

**返回值格式**：`list[dict]`，每个 dict 字段：

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `interval` | str | K线周期 | `"15m"` |
| `indicator_type` | str | 指标类型（donchian/bollinger/rsi/price_watch等） | `"donchian"` |
| `indicator_params` | dict | 指标参数 | `{"period": 20}` |
| `condition` | dict | 触发条件 | 见下方 |
| `trigger_type` | str | `"once"`（单次）或 `"continuous"`（持续） | `"continuous"` |

**condition 支持的运算符**：`gt`（大于）、`lt`（小于）、`gte`（≥）、`lte`（≤）、`eq`（等于）、`cross_above`（上穿）、`cross_below`（下穿）

**condition 操作数**：`{"field": "price"}` 引用价格，`{"field": "upper"}` 引用指标字段，`{"value": 50000}` 引用常量

```python
def get_watch_signals(self) -> list[dict]:
    return [
        {
            "interval": "15m",
            "indicator_type": "donchian",
            "indicator_params": {"period": 20},
            "condition": {
                "operator": "cross_above",
                "left": {"field": "price"},
                "right": {"field": "upper"},
            },
            "trigger_type": "continuous",
        }
    ]
```

#### 可用技术指标（来自 `indicators`）

> **重要**：以下所有指标函数均返回 `np.ndarray`，策略代码中必须用 `float(arr[-1])` 取最新值后才能比较。

- `sma(history, period=20) -> np.ndarray` — 简单移动平均
- `ema(history, period=12) -> np.ndarray` — 指数移动平均
- `rsi(history, period=14) -> np.ndarray` — 相对强弱指数
- `macd(history, fast=12, slow=26, signal_period=9) -> dict[str, np.ndarray]` → `{"macd", "signal", "histogram"}`
- `bollinger(history, period=20, std_dev=2.0) -> dict[str, np.ndarray]` → `{"upper", "middle", "lower"}`
- `atr(history, period=14) -> np.ndarray` — 平均真实波幅，history 为 K 线 dict 列表；也可用 `atr(highs, lows, closes, period=N)` 直接传 np.ndarray 数组
- `stoch(history, k_period=14, d_period=3) -> dict[str, np.ndarray]` → `{"k", "d"}`

#### OrderSignal 构造

```python
# 买入
self.ctx.buy(quantity=Decimal("0.01"), signal_name="rsi_cross")

# 卖出
self.ctx.sell(quantity=Decimal("0.01"), signal_name="take_profit")

# 平仓（卖出全部持仓）
self.ctx.close_position()
```

### 代码质量要求

- **必须使用 `@register_strategy()` 装饰器** — 否则回测引擎无法发现策略
- `on_bar` 签名必须是 `(self, kline: dict, history: list[dict])`，禁止使用其他签名
- **必须 `return` 信号** — `ctx.buy()` / `ctx.sell()` / `ctx.close_position()` 必须作为 `on_bar` 的返回值返回，禁止仅调用不 return
- `ctx` 通过 `self.ctx` 访问，禁止在 `on_bar` 内调用 `ctx.history()`
- 所有参数从 `self.ctx.params` 读取，支持运行时配置
- `params_schema` 定义所有可调参数及其类型/默认值
- 指标计算前检查历史数据长度，避免 NaN
- 买入前检查 `self.ctx.position == 0`，卖出前检查 `self.ctx.position > 0`
- 使用 `Decimal` 处理数量和价格
- 信号命名规范：`entry`、`exit`、`stop_loss`、`take_profit` 等
- 包含 docstring 描述策略逻辑

### 指标返回值处理（CRITICAL）

**所有指标函数均返回 `np.ndarray`，禁止直接与标量比较或参与布尔运算。**

错误写法（会抛出 `ValueError: The truth value of an array with more than one element is ambiguous`）：

```python
# ❌ 错误：atr_value 是 numpy 数组，不能与 0 比较
atr_value = atr(history, period=14)
if atr_value is None or atr_value == 0:  # ValueError!
    return None

# ❌ 错误：三元表达式仍可能返回 numpy 元素
atr_value = atr_array[-1] if len(atr_array) > 0 else None
if atr_value == 0:  # 如果 atr_array[-1] 仍是数组类型，会报错
    return None
```

正确写法：

```python
# ✅ 正确：先取最新值，再转 Python float
atr_array = atr(history, period=14)
if len(atr_array) == 0:
    return None
atr_value = float(atr_array[-1])

# ✅ 同理适用于所有指标
rsi_array = rsi(history, period=14)
rsi_current = float(rsi_array[-1])

ema_array = ema(history, period=20)
ema_current = float(ema_array[-1])

# ✅ macd 返回字典，每个值也是 np.ndarray
macd_result = macd(history)
macd_val = float(macd_result["macd"][-1])
signal_val = float(macd_result["signal"][-1])

# ✅ bollinger 同理
bb = bollinger(history)
upper = float(bb["upper"][-1])
middle = float(bb["middle"][-1])
lower = float(bb["lower"][-1])
```

**规则总结**：
1. 先用 `len(array) == 0` 检查空数组
2. 用 `[-1]` 取最新元素
3. 用 `float()` 转为 Python 原生标量
4. 之后才能安全使用 `== 0`、`is None`、`< >` 等比较运算
