---
name: quant-backtest
description: 使用 submit_backtest 提交异步回测任务，通过 get_task_result 查询结果，支持网格搜索，基于策略引擎执行加密货币策略回测
version: 3.0.0
---

# 量化策略回测技能

使用策略引擎进行加密货币策略回测，支持普通回测和网格搜索参数优化。**回测是异步长任务**，必须使用两步模式：提交任务 → 查询结果。

## 适用场景

- "帮我回测这个策略"
- "用 RSI + EMA 策略跑一下 BTC 日线回测"
- "这个策略在过去一年的表现如何"
- "参数优化：找出 RSI 最佳周期"
- "网格搜索：优化策略参数组合"
- "对比不同参数下的策略表现"

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

### 步骤 0：根据用户描述推断并确认策略

**用户通常不会提供精确的 `strategy_name`，需要根据描述推断。**

#### 0.1 从用户描述中提取策略特征

分析用户的回测请求，提取使用的指标和策略类型：

| 用户描述 | 推断 strategy_name |
|---------|-------------------|
| "RSI + EMA 策略" | `rsi_ema_strategy` |
| "布林带 RSI 超卖" | `bollinger_rsi_strategy` |
| "双均线交叉" | `ma_cross_strategy` |
| "MACD 交叉 + ATR" | `macd_atr_strategy` |
| "网格交易" | `grid_strategy` |
| "马丁格尔" | `martingale_strategy` |
| "趋势跟踪" | `trend_following_strategy` |

**命名格式**：`{指标1}_{指标2}_strategy`，全部小写英文。

#### 0.2 检查策略文件是否存在

策略统一存放在 `~/.tradelogx/strategies/` 目录下。

调用 `read_file` 工具确认文件存在：
```
read_file(
    file_path="~/.tradelogx/strategies/{推断的 strategy_name}.py",
    agent_name="quant"
)
```

#### 0.3 三种情况处理

1. **文件存在** → 使用该 strategy_name，继续步骤 1
2. **文件不存在，但用户已描述了具体策略逻辑** → 加载 `create-strategy` 技能创建策略，创建成功后再提交回测
3. **无法从描述中推断策略类型** → 询问用户策略的具体指标和逻辑

### 步骤 1：确认回测参数

收集以下回测配置：

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| strategy_name | 是 | — | 从步骤 0 确定的策略名称 |
| symbol | 否 | BTC/USDT | 交易品种 |
| timeframe | 否 | 1d | K 线周期（1m/5m/15m/30m/1h/4h/1d） |
| start_date | 否 | 30 天前 | 回测开始日期（ISO 格式，如 2024-01-01） |
| end_date | 否 | 今天 | 回测结束日期（ISO 格式，如 2025-04-15） |
| exchange | 否 | binance | 交易所 |
| initial_capital | 否 | 10000 | 初始资金（USDT） |
| commission_rate | 否 | 0.001 | 手续费率（0.1%） |
| parameters | 否 | {} | 策略参数字典（如 {"fast": 10, "slow": 30}） |
| grid_search | 否 | null | 网格搜索配置对象 |

**必须确认回测时间范围。** 如果用户未提及日期，主动询问：
> 请确认回测的时间范围，例如"最近 1 个月"、"2024 年全年"、"2023-06 到 2024-06"等。
> 默认使用最近 30 天。

如果用户提供了时间描述，转换为 `start_date` 和 `end_date`（ISO 格式，如 `2024-01-01`）。

### 步骤 1.1：网格搜索配置（可选）

如果用户要求参数优化或网格搜索，使用 `grid_search` 对象：

```
grid_search: {
  "enabled": true,
  "parameters": {
    "rsi_period": {"min": 10, "max": 30, "step": 5},
    "rsi_threshold": {"min": 20, "max": 40, "step": 5}
  },
  "sort_by": "sharpe_ratio", // sharpe_ratio, total_return_pct, win_rate
  "max_combinations": 100
}
```

### 步骤 2：提交回测任务

调用 `submit_backtest` 工具提交异步任务：

```
submit_backtest(
    strategy_name="{strategy_name}",
    symbol="BTC/USDT",
    timeframe="1d",
    start_date="2024-01-01",
    end_date="2025-04-15",
    initial_capital=10000,
    commission_rate=0.001,
    parameters={"fast": 10, "slow": 30},
    exchange="binance",
    grid_search={ // 可选
      "enabled": true,
      "parameters": {
        "rsi_period": {"min": 10, "max": 30, "step": 5}
      },
      "sort_by": "sharpe_ratio",
      "max_combinations": 50
    }
)
```

工具立即返回：

```json
{
    "task_id": "abc123-def456",
    "status": "PENDING",
    "message": "回测任务已提交，task_id=abc123-def456。策略=xxx 标的=BTC/USDT 周期=1d。使用 get_task_result 查询进度和结果。"
}
```

**收到 task_id 后，立即回复用户：**

> 回测任务已提交！
> - 任务ID: `abc123-def456`
> - 策略: {strategy_name}
> - 标的: BTC/USDT
> - 周期: 1d
> - 时间范围: 2024-01-01 ~ 2025-04-15
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
        "profit_factor": 2.1,
        "final_equity": 14520.0,
        "equity_curve": [...],
        "trades": [...],
        "benchmark": {
            "name": "Buy & Hold",
            "return_pct": 38.5
        }
    }
}
```

将结果格式化为回测报告（见下方模板）。

#### 网格搜索结果

```json
{
    "task_id": "abc123-def456",
    "status": "SUCCESS",
    "result": {
        "type": "grid_search",
        "best_params": {"rsi_period": 14, "rsi_threshold": 30},
        "best_score": 2.1,
        "best_score_metric": "sharpe_ratio",
        "results": [
            {
                "params": {"rsi_period": 10, "rsi_threshold": 25},
                "sharpe_ratio": 1.8,
                "total_return_pct": 35.2,
                "max_drawdown_pct": -15.3
            },
            {
                "params": {"rsi_period": 14, "rsi_threshold": 30},
                "sharpe_ratio": 2.1,
                "total_return_pct": 42.1,
                "max_drawdown_pct": -12.5
            }
        ]
    }
}
```

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

### 基准对比

| 策略 | 收益率 |
|------|--------|
| 策略本身 | {total_return_pct}% |
| Buy & Hold | {benchmark.return_pct}% |

### 分析与建议

[基于绩效指标的自然语言分析]
```

### 网格搜索报告

```
## 网格搜索结果

**任务ID**: {task_id}
**策略**: {strategy_name}
**最佳参数**: {best_params}
**最优指标**: {best_score} ({best_score_metric})

### 参数组合排名

| 参数组合 | 夏普比率 | 收益率 | 最大回撤 |
|----------|----------|--------|----------|
| {param_combo1} | {sharpe1} | {return1}% | {dd1}% |
| {param_combo2} | {sharpe2} | {return2}% | {dd2}% |

### 建议

[基于参数优化结果的建议]
```

## 完整执行示例

用户请求："用 EMA 交叉策略回测 BTC/USDT 日线，快线 10，慢线 30"

**第 1 轮（确认参数 + 提交任务）：**

1. 推断 strategy_name = `ema_cross_strategy`（从"EMA 交叉"提取）
2. 检查 `~/.tradelogx/strategies/ema_cross_strategy.py` 是否存在
   - 文件存在 → 继续
   - 文件不存在 → 加载 `create-strategy` 技能，先创建策略
3. 确认参数（标的 BTC/USDT、周期 1d、参数 fast=10 slow=30）
4. **确认回测时间范围**：如果用户未提及，主动询问并转换为 ISO 日期
5. 调用 `submit_backtest(strategy_name="ema_cross_strategy", symbol="BTC/USDT", timeframe="1d", parameters={"fast": 10, "slow": 30}, start_date="2025-03-15", end_date="2025-04-15")`
6. 收到 `task_id`，回复用户任务已提交（含时间范围）

**第 2 轮（用户查询结果）：**

1. 用户发送"回测结果出来了吗？"
2. 调用 `get_task_result(task_id="abc123")`
3. 如果 SUCCESS → 输出回测报告
4. 如果 STARTED → 告知仍在运行
5. 如果 FAILURE → 告知错误原因

## 策略文件存放位置

- **目录**：`~/.tradelogx/strategies/`
- **文件命名**：`{strategy_name}.py`
- **示例**：
  - `bollinger_rsi_strategy.py` → strategy_name = `bollinger_rsi_strategy`
  - `ema_cross_strategy.py` → strategy_name = `ema_cross_strategy`
  - `dual_ma_strategy.py` → strategy_name = `dual_ma_strategy`

## 回测结果字段详解

回测完成后，`result` 对象包含以下字段：

- `final_equity`: 最终资金
- `total_return_pct`: 总收益率 (%)
- `annualized_return_pct`: 年化收益率 (%)
- `sharpe_ratio`: 夏普比率
- `max_drawdown_pct`: 最大回撤 (%)
- `win_rate`: 胜率
- `total_trades`: 总交易次数
- `profit_factor`: 盈亏比
- `equity_curve`: 权益曲线数据点数组
- `trades`: 交易明细数组
- `benchmark`: 基准对比数据（如有）

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
- **依赖**：确保环境已安装所需回测依赖
- **策略创建**：策略文件统一写入 `~/.tradelogx/strategies/`，命名遵循 `{strategy_name}.py` 格式
- **网格搜索**：使用 `grid_search` 对象启用参数优化，支持多种指标排序
- **任务跟踪**：使用 TaskTracker 进行任务进度跟踪和状态管理