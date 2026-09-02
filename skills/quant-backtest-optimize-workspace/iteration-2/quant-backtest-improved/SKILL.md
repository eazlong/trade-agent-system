---
name: quant-backtest
description: 提交异步回测任务并自动查询结果，支持普通回测和网格搜索参数优化。当用户提到回测、策略测试、历史表现、参数优化、网格搜索、策略对比，或者想了解某个策略在过去的表现如何时，使用此技能。即使用户没有明确说"回测"，只要意图是评估策略的历史收益和风险，都应触发。
---

# 量化策略回测技能

回测在 Celery Worker 中异步执行（几秒到几分钟），流程分三步：**提交任务** → **自动安排定时查询** → **拿到结果后主动发送给用户**。

## 流程

### 1. 推断策略名称

用户通常不会给出精确的 `strategy_name`，需要从描述中推断。

**推断规则**：提取策略用到的指标，按 `{指标1}_{指标2}_strategy` 格式拼成小写英文名。例如：

- "RSI 均值回归" → `rsi_mean_reversion_strategy`
- "双均线交叉" → `ma_cross_strategy`
- "布林带 + RSI" → `bollinger_rsi_strategy`

**验证策略存在**：用 `read_file` 读取 `~/.tradelogx/strategies/{strategy_name}.py`。

- **文件存在** → 继续
- **文件不存在但用户描述了策略逻辑** → 先加载 `create-strategy` 技能帮用户创建策略，再提交回测
- **无法推断** → 询问用户策略的具体指标和逻辑

### 2. 确认参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| strategy_name | 是 | — | 步骤 1 确定的名称 |
| symbol | 否 | BTC/USDT | 交易品种 |
| timeframe | 否 | 1d | K 线周期（1m/5m/15m/30m/1h/4h/1d） |
| start_date | 否 | 30 天前 | 开始日期（ISO 格式） |
| end_date | 否 | 今天 | 结束日期（ISO 格式） |
| exchange | 否 | binance | 交易所 |
| initial_capital | 否 | 10000 | 初始资金（USDT） |
| commission_rate | 否 | 0.001 | 手续费率 |
| parameters | 否 | {} | 策略参数字典 |
| benchmark | 否 | null | 基准策略名（如 buy_and_hold），用于对比 |

**时间范围**：如果用户没提日期，主动询问。把"最近 3 个月"、"2024 全年"等描述转换为 ISO 日期。

**策略参数示例**（`parameters` 字段）：

```json
// EMA 交叉策略
{"ma_fast": 10, "ma_slow": 30}

// RSI 策略
{"rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30}

// 布林带 + RSI
{"bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_oversold": 30}

// MACD + ATR
{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "atr_period": 14}

// 均线交叉 + 成交量
{"fast_period": 5, "slow_period": 20, "volume_multiplier": 1.5}
```

具体参数名取决于策略代码中 `params_schema` 的定义，可用 `read_file` 查看策略源文件获取。

**网格搜索**（用户要求参数优化时）：添加 `grid_search` 对象：

```json
{
  "enabled": true,
  "parameters": {
    "rsi_period": {"min": 10, "max": 30, "step": 5},
    "rsi_threshold": {"min": 20, "max": 40, "step": 5}
  },
  "sort_by": "sharpe_ratio",
  "max_combinations": 100
}
```

`sort_by` 可选：`sharpe_ratio`、`total_return_pct`、`win_rate`。

### 3. 提交回测任务

```
submit_backtest(
    strategy_name="rsi_strategy",
    symbol="BTC/USDT",
    timeframe="1d",
    start_date="2024-01-01",
    end_date="2025-04-15",
    initial_capital=10000,
    commission_rate=0.001,
    parameters={"rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30},
    exchange="binance"
)
```

收到 `task_id` 后，回复用户任务已提交：

> 回测任务已提交！
> - 任务ID: `{task_id}`
> - 策略: {strategy_name} / 标的: {symbol} / 周期: {timeframe}
> - 时间范围: {start_date} ~ {end_date}
>
> 已安排 2 分钟后自动查询结果，完成后会直接发送给你。

### 4. 自动安排结果查询（关键步骤）

提交回测后，**立即**用 `submit_scheduled_task` 安排一个 30 秒后的一次性任务，让 Agent 自动查询结果并处理：

```
submit_scheduled_task(
    agent_name="quant",
    message="请查询回测任务 task_id={task_id} 的结果。调用 get_task_result(task_id=\"{task_id}\")。如果状态是 SUCCESS，将结果格式化为回测报告发送给用户。如果状态是 FAILURE，分析错误原因，修正参数后用 submit_backtest 重新提交回测，并再次安排定时查询。如果仍是 PENDING/STARTED，再安排一个 2 分钟后的定时任务继续查询。",
    run_at="now+30s"
)
```

这样用户无需手动追问，Agent 会在后台自动轮询直到拿到终态结果。

**重试策略**：

- **PENDING/STARTED**（还在跑）→ 再 `submit_scheduled_task(run_at="now+30s”)` 继续等
- **SUCCESS** → 格式化报告，发送给用户
- **FAILURE** → 分析错误，修正后重新 `submit_backtest`，再安排新的定时查询
- **最多重试 3 次**，超过后告知用户失败原因并停止

### 5. 查询结果（get_task_result）

当定时任务触发或用户主动询问时，调用：

```
get_task_result(task_id="xxx")
```

根据状态处理：

- **PENDING / STARTED** → 再安排一个 `now+2m` 的定时任务继续查
- **SUCCESS** → 格式化为回测报告（见下方模板）
- **FAILURE** → 分析错误原因，修正参数后重新提交

**常见 FAILURE 原因及修正**：

| 错误 | 修正方式 |
|------|----------|
| "未找到策略 xxx" | 检查 strategy_name 拼写，确认 `~/.tradelogx/strategies/` 下文件存在 |
| "K线数据不足" | 缩短时间范围或换更大的 timeframe |
| "参数 xxx 不存在" | 用 `read_file` 查看策略的 `params_schema`，修正参数名 |
| "策略执行异常" | 用 `read_file` 检查策略代码逻辑，可能指标计算出错 |

## 报告模板

### 普通回测

```
## 回测报告

**策略**: {strategy_name} | **品种**: {symbol} | **周期**: {timeframe}
**时间**: {start_date} ~ {end_date} | **初始资金**: {initial_capital} USDT

| 指标 | 数值 |
|------|------|
| 总收益率 | {total_return_pct}% |
| 年化收益率 | {annualized_return_pct}% |
| 夏普比率 | {sharpe_ratio} |
| 最大回撤 | {max_drawdown_pct}% |
| 胜率 | {win_rate}% |
| 总交易次数 | {total_trades} |
| 盈亏比 | {profit_factor} |

**策略参数**: {parameters}

### 分析
[基于指标给出简要分析：收益风险比是否合理、是否存在过拟合迹象、建议的优化方向]
```

如有 benchmark 对比，加上：

```
| 基准对比 | 收益率 |
|----------|--------|
| 策略 | {total_return_pct}% |
| Buy & Hold | {benchmark.return_pct}% |
```

### 网格搜索

```
## 网格搜索结果

**最佳参数**: {best_params}（{best_score_metric} = {best_score}）

| 排名 | 参数组合 | 夏普 | 收益率 | 回撤 |
|------|----------|------|--------|------|
| 1 | {params} | {sharpe} | {return}% | {dd}% |
| 2 | ... | ... | ... | ... |

### 建议
[推荐的最佳参数、是否需要样本外验证、过拟合风险提示]
```

## 策略文件

- **目录**：`~/.tradelogx/strategies/`
- **命名**：`{strategy_name}.py`
- **发现**：`StrategyRegistry.discover()` 扫描该目录自动注册

## 前端展示

- `/backtest` — 回测列表
- `/backtest/{id}` — 详情（权益曲线、回撤曲线、交易日志）
- `GET /api/backtest/results/<id>/detail/` — 摘要 + 曲线数据
- `GET /api/backtest/results/<id>/trades/` — 分页交易记录

## 注意事项

- 手续费默认 0.1%，建议设置 0.01% 滑点使回测更贴近实盘
- 权益曲线超过 2000 点会自动降采样
- 网格搜索的 `max_combinations` 控制最大参数组合数，避免耗时过长
- 参数优化后务必做样本外验证，避免过拟合
