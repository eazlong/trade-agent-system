---
name: quant-backtest
description: 使用 submit_backtest 提交异步回测任务，通过 get_task_result 查询结果，基于 VectorBT 框架执行策略回测
version: 2.0.0
---

# 量化策略回测技能

使用 VectorBT 进行加密货币策略回测。**回测是异步长任务**，必须使用两步模式：提交任务 → 查询结果。

## 适用场景

- "帮我回测这个策略"
- "用 RSI + EMA 策略跑一下 BTC 日线回测"
- "这个策略在过去一年的表现如何"
- "参数优化：找出 RSI 最佳周期"

## 关键规则（必须遵守）

**回测是耗时任务（几秒到几分钟），严禁同步等待结果。**

正确流程只有两步：

1. 调用 `submit_backtest` 工具提交任务 → 立即获得 `task_id`
2. 告知用户任务已提交，等待用户下次询问时再调用 `get_task_result(task_id=xxx)` 查询

**禁止行为：**
- 不要在单轮对话中循环调用 `get_task_result` 轮询结果
- 不要试图在工具调用循环中同步完成回测
- 收到 PENDING/STARTED 状态时，回复用户"任务正在运行"即可结束当前轮

## 回测流程

### 步骤 1：确认策略参数

收集以下回测配置：

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| strategy_name | 是 | — | 策略名称（rsi_cross, ema_cross, macd_cross, bollinger 等） |
| symbol | 否 | BTC/USDT | 交易品种 |
| timeframe | 否 | 1d | K 线周期（1m/5m/15m/30m/1h/4h/1d） |
| exchange | 否 | binance | 交易所 |
| initial_capital | 否 | 10000 | 初始资金（USDT） |
| commission_rate | 否 | 0.001 | 手续费率（0.1%） |
| parameters | 否 | {} | 策略参数字典（如 {"fast": 10, "slow": 30}） |

如果用户未提供完整信息，先询问确认。

### 步骤 2：提交回测任务

调用 `submit_backtest` 工具提交异步任务：

```
submit_backtest(
    strategy_name="ema_cross",
    symbol="BTC/USDT",
    timeframe="1d",
    initial_capital=10000,
    commission_rate=0.001,
    parameters={"fast": 10, "slow": 30},
    exchange="binance"
)
```

工具立即返回：

```json
{
    "task_id": "abc123-def456",
    "status": "PENDING",
    "message": "回测任务已提交，task_id=abc123-def456。策略=ema_cross 标的=BTC/USDT 周期=1d。使用 get_task_result 查询进度和结果。"
}
```

**收到 task_id 后，立即回复用户：**

> 回测任务已提交！
> - 任务ID: `abc123-def456`
> - 策略: EMA交叉
> - 标的: BTC/USDT
> - 周期: 1d
>
> 回测正在后台运行，请稍后发送"查询回测结果"获取结果。

### 步骤 3：查询回测结果（用户主动触发时）

当用户询问结果时，调用 `get_task_result` 查询：

```
get_task_result(task_id="abc123-def456")
```

可能的返回：

#### 任务运行中（PENDING / STARTED）

```json
{
    "task_id": "abc123-def456",
    "status": "STARTED",
    "info": {"step": "running_backtest", "bars": 500},
    "message": "任务仍在运行中（STARTED），请稍后再次查询。"
}
```

回复用户：任务仍在运行中，请稍后再查询。

#### 任务成功（SUCCESS）

```json
{
    "task_id": "abc123-def456",
    "status": "SUCCESS",
    "result": {
        "total_return_pct": 45.2,
        "annualized_return_pct": 52.3,
        "sharpe_ratio": 1.85,
        "max_drawdown_pct": -18.5,
        "win_rate": 0.62,
        "total_trades": 28,
        "profit_factor": 2.1
    }
}
```

将结果格式化为回测报告（见下方模板）。

#### 任务失败（FAILURE）

```json
{
    "task_id": "abc123-def456",
    "status": "FAILURE",
    "error": "未找到策略 ema_cross"
}
```

告知用户失败原因，建议修正参数后重新提交。

### 步骤 4：输出回测报告

将 SUCCESS 结果格式化为可读报告：

```
## 回测报告

**任务ID**: {task_id}
**策略**: {strategy_name}
**品种**: {symbol}
**周期**: {timeframe}
**初始资金**: {initial_capital} USDT

### 绩效指标

| 指标 | 数值 |
|------|------|
| 总收益率 | {total_return_pct}% |
| 年化收益率 | {annualized_return_pct}% |
| 夏普比率 | {sharpe_ratio} |
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

用户请求："用 EMA 交叉策略回测 BTC/USDT 日线，快线 10，慢线 30"

**第 1 轮（提交任务）：**

1. 确认参数齐全（策略名、标的、周期、参数）
2. 调用 `submit_backtest(strategy_name="ema_cross", symbol="BTC/USDT", timeframe="1d", parameters={"fast": 10, "slow": 30})`
3. 收到 `task_id`，回复用户任务已提交

**第 2 轮（用户查询结果）：**

1. 用户发送"回测结果出来了吗？"
2. 调用 `get_task_result(task_id="abc123")`
3. 如果 SUCCESS → 输出回测报告
4. 如果 STARTED → 告知仍在运行
5. 如果 FAILURE → 告知错误原因

## 支持的策略类型

| strategy_name | 说明 | 默认参数 |
|---------------|------|----------|
| ema_cross | EMA 均线交叉 | `{"fast": 10, "slow": 30}` |
| rsi_cross | RSI 超买超卖 | `{"rsi_period": 14, "oversold": 30, "overbought": 70}` |
| macd_cross | MACD 交叉 | `{"fast": 12, "slow": 26, "signal": 9}` |
| bollinger | 布林带回归 | `{"window": 20, "std_dev": 2.0}` |

## 前端展示

回测结果可通过前端页面查看：

- **回测列表**：`/backtest` — 展示所有历史回测的摘要表格
- **回测详情**：`/backtest/{id}` — 权益曲线、回撤曲线（Tab 切换）、分页交易表格
- **API**：
  - `GET /api/backtest/results/<id>/detail/` — 返回摘要 + equity_curve + drawdown_curve
  - `GET /api/backtest/results/<id>/trades/?page=1&page_size=50` — 分页交易日志

## 注意事项

- **异步模式**：回测任务在 Celery Worker 中异步执行，不影响 Agent 响应其他用户消息
- **数据质量**：回测前检查 K 线数据完整性，确保无大面积缺口
- **手续费**：加密货币默认手续费 0.1%
- **滑点**：建议设置 0.01% 滑点使回测更贴近实际
- **过拟合**：参数优化后务必做样本外验证，避免过拟合
- **模型字段**：`BacktestResult` 的 `strategy` 是外键，关联 `trading.Strategy` 表；`initial_capital` 和 `final_capital` 使用 `DecimalField`
- **降采样**：权益曲线 > 2000 点时自动等间隔降采样
- **交易批量写入**：使用 `bulk_create(batch_size=500)` 高效写入交易日志
- **依赖**：确保环境已安装 `vectorbt`（`pip install vectorbt`）和 `pandas-ta`
