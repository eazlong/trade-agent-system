# Watch Signals 推导规则

> 由 SKILL.md 阶段 3.2 引用。将策略入场条件推导为最小周期信号。
>
> **必须实现 `get_watch_signals`，禁止返回 `[]`。** 详见 SKILL.md 清单 #7。

## 1. 简化原则

**只保留单指标条件**，复合条件在 `on_bar()` 中完整验证：

| 入场条件 | watch signal | on_bar 验证 |
|---------|-------------|------------|
| RSI < 30 且 EMA向上 | RSI < 30 | RSI < 30 && EMA_slope > 0 |
| 价格突破布林带上轨 且 MACD金叉 | 价格突破布林带上轨 | price > bb_upper && macd > signal |
| EMA_fast 上穿 EMA_slow | EMA_fast 上穿 EMA_slow | 完整条件（单指标已足够） |

## 2. 指标类型映射

将策略中使用的指标映射为 `indicator_type`：

| 策略指标 | indicator_type | indicator_params |
|---------|---------------|-----------------|
| RSI | `"rsi"` | `{"period": 14}` |
| EMA | `"ema"` | `{"period": 20}` |
| 布林带 | `"bollinger"` | `{"period": 20, "std_dev": 2.0}` |
| MACD | `"macd"` | `{"fast": 12, "slow": 26, "signal_period": 9}` |
| 唐奇安通道 | `"donchian"` | `{"period": 20}` |
| ATR | `"atr"` | `{"period": 14}` |

## 3. 条件运算符映射

将入场条件转换为 `condition` 格式：

| 条件描述 | operator | left | right |
|---------|---------|------|-------|
| RSI < 30 | `"lt"` | `{"field": "rsi"}` | `{"value": 30}` |
| RSI > 70 | `"gt"` | `{"field": "rsi"}` | `{"value": 70}` |
| 价格上穿布林带上轨 | `"cross_above"` | `{"field": "price"}` | `{"field": "upper"}` |
| EMA 下穿 EMA_slow | `"cross_below"` | `{"field": "ema"}` | `{"field": "ema_slow"}` |
| 价格 > 50000 | `"gt"` | `{"field": "price"}` | `{"value": 50000}` |

**支持的运算符**：`gt`（大于）、`lt`（小于）、`gte`（≥）、`lte`（≤）、`eq`（等于）、`cross_above`（上穿）、`cross_below`（下穿）

**操作数**：`{"field": "price"}` 引用价格，`{"field": "upper"}` 引用指标字段，`{"value": 50000}` 引用常量

## 4. 周期选择规则

使用策略的**最小K线周期**作为 `interval`：

- 策略使用 4h + 1d → watch signal 使用 `"4h"`
- 策略仅使用 1h → watch signal 使用 `"1h"`
- 多时间框架策略 → 使用最小周期（如 15m + 1h → `"15m"`）

## 5. trigger_type 选择

- **持续信号**（如 RSI < 30）→ `"continuous"`
- **突破信号**（如 价格上穿布林带）→ `"once"`（首次触发后移除）

## 6. 多条件策略示例

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

## 实盘机制说明

当策略部署到实盘/测试网时，`LiveStrategyRunner` 会调用 `get_watch_signals` 将最小周期信号注册到 `SignalMonitor`，实现两阶段信号检测：

```
SignalMonitor 定时检查单指标条件（轻量初筛）
  → 触发 → Redis List 事件 → LiveStrategyRunner 消费
  → 运行完整 on_bar() 验证多指标组合 → dispatch 交易信号
```

**典型场景**：策略需要"4h EMA 趋势向上 + 15m 价格突破 Donchian 上轨"，则只需将 15m Donchian 突破注册为 watch signal，4h EMA 趋势在 `on_bar()` 中完整验证。

## 格式要求

**必须返回 `list[dict]`，每个 dict 包含五个字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `interval` | str | K线周期（如 `"15m"`, `"1h"`） |
| `indicator_type` | str | 指标类型（rsi/ema/bollinger/macd/donchian/atr） |
| `indicator_params` | dict | 指标参数（如 `{"period": 14}`） |
| `condition` | dict | 触发条件（见第 3 节运算符映射） |
| `trigger_type` | str | `"once"`（单次）或 `"continuous"`（持续） |

**实现要求见 SKILL.md 生成代码必查清单 #7。**
