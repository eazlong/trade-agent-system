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

确认后，生成策略 Python 文件。

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
        # self.ctx.buy(...)  # 买入信号
        # self.ctx.sell(...) # 卖出信号

        # 买入前检查：self.ctx.position == 0
        # 卖出前检查：self.ctx.position > 0

        return None

    def on_start(self) -> None:
        """策略启动时调用（初始化指标状态等）"""

    def on_stop(self) -> None:
        """策略停止时调用"""
```

### 关键 API 参考

#### StrategyContext（通过 self.ctx 访问）

| 方法/属性 | 说明 |
|------|------|
| `self.ctx.position` | 当前持仓量 |
| `self.ctx.balance` | 当前余额 |
| `self.ctx.buy(quantity, signal_name)` | 发买入信号 |
| `self.ctx.sell(quantity, signal_name)` | 发卖出信号 |
| `self.ctx.close_position()` | 平仓信号 |
| `self.ctx.params` | 策略参数字典 |

#### BaseStrategy 便捷方法

| 方法 | 说明 |
|------|------|
| `self.history(symbol?, timeframe?, n?)` | 获取历史 K 线（仅实盘用） |
| `self.last_bar(symbol?, timeframe?)` | 获取最后一根 K 线 |
| `self.higher_tf()` | 获取更高周期 DataFeed |
| `self.lower_tf()` | 获取更低周期 DataFeed |

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

- `on_bar` 签名必须是 `(self, kline: dict, history: list[dict])`，禁止使用其他签名
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
