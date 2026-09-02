---
name: quant-backtest
description: 提交异步回测任务并自动查询结果，支持普通回测和网格搜索参数优化。当用户提到回测、策略测试、历史表现、参数优化、网格搜索、策略对比，或想了解某策略过去的表现时触发。即使没说"回测"，只要意图是评估策略历史收益和风险，都应使用此技能。
autonomous: true
---

# 量化策略回测

回测在 Celery Worker 中异步执行（几秒到几分钟），流程：**语义匹配策略** → **确认参数** → **提交任务+自动轮询** → **发送结果**。

## Auto-mode

`autonomous: true`（默认）时：

- 用户未提供的参数直接用默认值，不主动询问
- 所有"通知用户结果"环节保留
- 不跨越 skill 边界自动触发其他 skill（如 score < 80 时不自动触发 create-strategy）

## 流程

### 1. 语义匹配策略

通过自然语言描述找到最匹配的策略。

1. **获取策略列表**：调用 `list_strategies()`
2. **LLM 匹配**：将用户描述 + 策略列表传给 LLM，返回 `{"name": "策略名", "score": 0-100}`
3. **判断**：
   - `score >= 80` → 使用返回的 `name` 提交回测
   - `score < 80` 或 `name = null` → 告知用户"未找到匹配策略"，建议用 create-strategy 创建，**流程结束**

**绝不要**：凭猜测或记忆填 strategy_name，一律通过 `list_strategies()` + LLM 匹配确认。

### 2. 确认参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| strategy_name | 是 | — | 步骤 1 确定 |
| symbol | 否 | BTC/USDT | 交易品种 |
| timeframe | 否 | 1d | K 线周期（1m/5m/15m/30m/1h/4h/1d） |
| start_date | 否 | 30 天前 | ISO 格式 |
| end_date | 否 | 今天 | ISO 格式 |
| exchange | 否 | binance | 交易所 |
| initial_capital | 否 | 10000 | 初始资金（USDT） |
| commission_rate | 否 | 0.001 | 手续费率 |
| parameters | 否 | {} | 策略参数（见 [PARAMS.md](PARAMS.md)） |
| benchmark | 否 | null | 基准策略名（如 buy_and_hold） |

把"最近 3 个月"、"2024 全年"等描述转换为 ISO 日期。

网格搜索配置见 [PARAMS.md](PARAMS.md)。

### 3. 提交回测 + 自动轮询

调用 `submit_backtest(...)` 提交任务，收到 `task_id` 后：

1. **回复用户**：
   > 回测任务已提交！
   > - 任务ID: `{task_id}`
   > - 策略: {strategy_name} / 标的: {symbol} / 周期: {timeframe}
   > - 时间范围: {start_date} ~ {end_date}
   >
   > 已安排 2 分钟后自动查询结果，完成后会直接发送给你。

2. **立即安排定时查询**：
   ```
   submit_scheduled_task(
       agent_name="quant",
       message="请查询回测任务 task_id={task_id} 的结果。调用 get_task_result(task_id=\"{task_id}\")。如果状态是 SUCCESS，将结果格式化为回测报告（见 [TEMPLATES.md](TEMPLATES.md)）使用 notify_user 发送给用户。如果状态是 FAILURE，分析错误原因并按下方错误对照表修正后重新提交；注意：若错误是\"未找到策略\"，必须用 read_file 读取 strategies/{文件名}.py 并把类里的 name=\"...\" 值原样作为 strategy_name 重新提交，绝不能改用 CamelCase 类名或猜测的变体。如果仍是 PENDING/STARTED，再安排一个 2 分钟后的定时任务继续查询（使用与当前 message 完全一样的 message）",
       run_at="now+2m"
   )
   ```

**重试策略**（最多 10 次）：

- **PENDING/STARTED** → 再安排 `now+2m` 定时任务
- **SUCCESS** → 格式化报告，发送用户
- **FAILURE** → 按下方错误对照表修正后重新提交，再安排新的定时查询

**常见 FAILURE 修正**：

| 错误 | 修正 |
|------|------|
| "未找到策略 xxx" | `read_file` 读 `strategies/{文件名}.py`，把类里 `name = "..."` 原样重新提交。**不要**用 CamelCase 类名 |
| "K线数据不足" | 缩短时间范围或换更大 timeframe |
| "参数 xxx 不存在" | `read_file` 查看策略 `params_schema`，修正参数名 |
| "策略执行异常" | `read_file` 检查策略代码逻辑 |

超过 10 次重试后告知用户超时，让用户稍后主动查询。

## 策略文件

- **目录**：`~/.tradelogx/strategies/`（引擎按类的 `name` 属性自动注册）
- **read_file 路径**：`strategies/{文件名}.py`（相对 `~/.tradelogx/`），带 `agent_name="quant"`
- **注册名** = 类的 `name = "..."` 属性值，以属性值为准

## 注意事项

- 手续费默认 0.1%，建议设 0.01% 滑点使回测更贴近实盘
- 权益曲线超过 2000 点自动降采样
- 网格搜索 `max_combinations` 控制最大参数组合数
