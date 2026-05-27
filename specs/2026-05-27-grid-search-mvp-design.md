# Grid Search MVP 设计文档

**日期**: 2026-05-27
**状态**: MVP
**范围**: 全量网格搜索 + 策略内置 ranges + 单 Celery task 批量执行 + 定时任务入口 + Agent 对话入口

## 1. 概述

为回测系统增加参数网格搜索能力：用户在提交回测任务时，可指定策略参数的搜索范围，系统自动生成所有参数组合并批量执行回测，最终按指定指标排序呈现结果。

## 2. 数据模型

### 2.1 扩展 BacktestResult

在现有 `BacktestResult` 上新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `grid_search_id` | UUID (null) | 所属网格搜索任务 ID |
| `param_combination` | JSONField | 当前回测使用的参数组合，如 `{"ma_fast": 7, "ma_slow": 20}` |
| `is_grid_search` | BooleanField | 是否为网格搜索的子回测 |

### 2.2 新增 GridSearchJob 模型

```python
class GridSearchJob(models.Model):
    id = UUIDField(primary_key)
    user = ForeignKey(AUTH_USER_MODEL, null=True)
    strategy = ForeignKey("trading.Strategy")
    symbol = CharField(32)
    timeframe = CharField(8)
    start_date / end_date = DateField
    initial_capital = DecimalField
    commission_rate = DecimalField
    search_config = JSONField  # 详见 3.1
    status = CharField  # pending / running / completed / failed / cancelled
    total_combinations = IntegerField
    completed_combinations = IntegerField(default=0)
    best_result = ForeignKey("backtest.BacktestResult", null=True)
    sort_by = CharField  # 排序指标：sharpe_ratio / total_return_pct / win_rate
    created_at / updated_at
```

### 2.3 search_config 结构

```json
{
  "parameters": {
    "ma_fast": {"type": "int", "min": 5, "max": 20, "step": 1},
    "ma_slow": {"type": "int", "min": 10, "max": 60, "step": 5}
  },
  "sort_by": "sharpe_ratio",
  "max_combinations": 100
}
```

## 3. 参数区间定义

扩展策略的 `params_schema`，增加 `range` 可选字段：

```python
class MyStrategy(BaseStrategy):
    params_schema = {
        "ma_fast": {
            "type": "number",
            "default": 7,
            "range": {"type": "int", "min": 5, "max": 20, "step": 1}
        },
        "ma_slow": {
            "type": "number",
            "default": 25,
            "range": {"type": "int", "min": 10, "max": 60, "step": 5}
        }
    }
```

- 有 `range` 的参数才会被纳入网格搜索
- 无 `range` 的参数使用 `default` 值固定
- LLM Agent 可在用户未指定范围时，根据策略 `params_schema` 的 range 自动推断并推荐

## 4. 核心组件

### 4.1 GridSearchRunner

文件: `apps/strategy_engine/grid_search.py`

职责：

1. **组合生成** — 读取 `params_schema.range`，用笛卡尔积生成所有参数组合
2. **批量执行** — 循环遍历参数组合，逐个调用 `BacktestEngine`（同进程内执行，不走 Celery 拆分）
3. **进度追踪** — 通过 `TaskTracker` 推送当前进度、当前参数组合、当前指标
4. **结果排序** — 所有子回测完成后按 `sort_by` 指标排序，标记最优结果
5. **取消检查** — 每完成一个组合后检查 `GridSearchJob.status`，如为 cancelled 则提前终止

关键设计决策：**MVP 阶段单 Celery task 内批量执行**，原因：
- 简化实现，避免 Celery group/chain 的复杂性
- 组合数通过 `max_combinations` 限制（≤100）
- 单个回测通常 <10s，100 组合约 15 分钟，在 Celery task 超时范围内
- 第二期引入智能拆分时再改为多 task 并行

### 4.2 GridSearch 工具扩展

扩展 `apps/agent/tools/backtest.py` 的 `SubmitBacktestTool`：

在 `parameters_schema` 新增可选的 `grid_search` 字段：

```json
{
  "grid_search": {
    "enabled": true,
    "parameters": {
      "ma_fast": {"min": 5, "max": 20, "step": 1},
      "ma_slow": {"min": 10, "max": 60, "step": 5}
    },
    "sort_by": "sharpe_ratio"
  }
}
```

当 `grid_search.enabled == true` 时：
- 创建 `GridSearchJob` 记录
- 调用 `run_grid_search_task.delay(job_id)` 异步执行
- 返回 `grid_search_id` 给用户

### 4.3 新增 Celery Task: run_grid_search_task

文件: `apps/backtest/tasks.py`

```python
@app.task(bind=True, max_retries=1, acks_late=True, track_started=True)
def run_grid_search_task(self, job_id: str, user_id: str) -> dict:
    """执行网格搜索任务，批量回测所有参数组合。"""
```

任务流程：
1. 加载 `GridSearchJob`，生成参数组合列表
2. 对每个组合调用 `StrategyRunner.run_backtest()`
3. 每次完成后更新 `completed_combinations` 和进度
4. 全部完成后排序并标记 `best_result`
5. 通过 `TaskTracker` 推送最终结果摘要

## 5. REST API

### 5.1 新增端点

```
POST /api/backtest/grid-search/
```

Request body:

```json
{
  "strategy_id": "uuid",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "grid_search": {
    "parameters": {
      "ma_fast": {"min": 5, "max": 20, "step": 1},
      "ma_slow": {"min": 10, "max": 60, "step": 5}
    },
    "sort_by": "sharpe_ratio"
  }
}
```

Response:

```json
{
  "grid_search_id": "uuid",
  "task_id": "uuid",
  "total_combinations": 96,
  "message": "网格搜索任务已提交"
}
```

### 5.2 查询端点

```
GET /api/backtest/grid-search/<job_id>/
GET /api/backtest/grid-search/<job_id>/results/?sort=sharpe_ratio&page=1
```

返回 GridSearchJob 状态及所有子回测结果。

## 6. Agent 对话入口

### 6.1 Agent 识别

SupervisorAgent 识别到网格搜索意图后，路由到 QuantAgent。识别关键词：
- "网格搜索" / "grid search" / "参数优化" / "参数扫描"
- "帮我跑一下 X 参数从 A 到 B"

### 6.2 Agent 工具调用

QuantAgent 通过扩展后的 `submit_backtest` 工具调用，当检测到 `grid_search` 参数时自动走网格搜索流程。

如果用户未指定参数范围，Agent 从策略 `params_schema` 的 `range` 字段提取并展示给用户确认。

## 7. 定时任务入口

### 7.1 GridSearchSchedule 模型

```python
class GridSearchSchedule(models.Model):
    """周期性网格搜索任务配置"""
    name = CharField(64)
    strategy = ForeignKey("trading.Strategy")
    symbol = CharField(32)
    timeframe = CharField(8)
    lookback_days = IntegerField(default=30)  # 回测窗口：过去 N 天
    search_config = JSONField  # 同 GridSearchJob.search_config
    cron_expression = CharField(32)  # crontab 格式
    is_active = BooleanField(default=True)
    created_at / updated_at
```

### 7.2 Celery Beat 调度

在 `celery_app.py` 新增动态调度器：

```python
@app.task
def run_scheduled_grid_searches():
    """扫描所有活跃的 GridSearchSchedule，提交网格搜索任务。"""
    from apps.backtest.models import GridSearchSchedule
    for schedule in GridSearchSchedule.objects.filter(is_active=True):
        # 创建 GridSearchJob 并提交 Celery task
        ...
```

Beat 配置：每 6 小时执行一次扫描。

## 8. 前端展示（MVP 最小化）

在现有回测列表页增加：

- 网格搜索任务卡片（显示 total/completed 进度条）
- 点击展开子回测结果表格，可按指标排序
- 标注最优参数组合

## 9. 错误处理

- **组合数超限**：超过 `max_combinations`（默认 100）时拒绝执行，返回提示
- **策略无 range 定义**：返回错误，提示用户在策略中定义 range 或由 LLM 推荐
- **中途取消**：已完成的结果保留，job 状态标记为 cancelled
- **单个回测失败**：记录错误到对应 BacktestResult，继续执行下一个组合

## 10. 测试计划

- 单元测试：组合生成逻辑（笛卡尔积、边界值）
- 集成测试：GridSearchRunner 端到端（用 mock 策略 + 固定 OHLCV 数据）
- Celery task 测试：run_grid_search_task 进度更新、取消逻辑
- API 测试：创建/查询网格搜索任务的 HTTP 响应

## 11. 迁移计划

1. 新建 migration：BacktestResult 新增 3 字段
2. 新建 migration：GridSearchJob 模型
3. 新建 migration：GridSearchSchedule 模型
4. 不破坏现有回测功能，所有新增字段 nullable
