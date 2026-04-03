# 回测系统架构设计

> 版本：v1（第1轮）
> 负责架构师：架构师B
> 文档时间：2026-03-27

---

## 1. 设计目标与边界

### 1.1 设计目标

1. **多引擎适配**：加密货币使用 VectorBT，股票（A股/美股）使用 Backtrader，对外统一接口
2. **数据标准化**：所有市场数据进入回测前统一转换为 UnifiedBar 格式，屏蔽市场差异
3. **异步执行**：回测任务不阻塞 Web 服务器，通过 Redis Stream 任务队列异步处理
4. **结果可解读**：回测结果包含完整绩效指标，支持智能解读层（供 Agent 分析）
5. **数据缓存**：历史 K 线数据本地缓存，避免重复下载，InfluxDB 存储时序数据

### 1.2 系统边界

- **包含**：回测引擎执行、历史数据获取与缓存、数据标准化、绩效计算、结果存储
- **不包含**：策略逻辑定义（属于策略系统）、实盘执行（属于交易系统）、Agent 调度（属于 Agent 系统）
- **集成点**：接受来自 QuantEngineerAgent 的回测任务；向 Agent 系统返回结构化结果；与交易系统共享 UnifiedBar 数据层

---

## 2. 核心模块图（ASCII）

```
┌──────────────────────────────────────────────────────────────────────┐
│                          回测系统边界                                 │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                   UnifiedBacktestEngine（统一引擎门面）           │ │
│  │                                                                   │ │
│  │   run_backtest(strategy, unified_bars, config) -> BacktestResult │ │
│  │                                                                   │ │
│  │   ┌───────────────────┐        ┌───────────────────────────────┐ │ │
│  │   │  VectorBTEngine   │        │    BacktraderEngine           │ │ │
│  │   │  (加密货币)        │        │    (A股/美股/港股/期货)        │ │ │
│  │   │                   │        │                               │ │ │
│  │   │  vectorbt库封装    │        │    backtrader库封装           │ │ │
│  │   │  高性能向量化计算   │        │    事件驱动回测               │ │ │
│  │   └───────────────────┘        └───────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                     数据适配层                                    │ │
│  │                                                                   │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  ┌────────┐ │ │
│  │  │BinanceAdapter│  │  OKXAdapter  │  │AKShareAda  │  │yfinAdap│ │ │
│  │  │(CCXT)        │  │(CCXT)        │  │(A股)       │  │(美股)  │ │ │
│  │  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  └───┬────┘ │ │
│  │         └─────────────────┴──────────────────┴────────────┘      │ │
│  │                                    │                              │ │
│  │                                    ▼                              │ │
│  │                    ┌───────────────────────────┐                 │ │
│  │                    │  UnifiedBar 标准化转换器   │                 │ │
│  │                    └───────────────────────────┘                 │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  ┌───────────────────────────┐  ┌──────────────────────────────────┐ │
│  │      数据缓存层            │  │         绩效分析引擎              │ │
│  │                           │  │                                  │ │
│  │  InfluxDB（时序K线数据）   │  │  PerformanceAnalyzer             │ │
│  │  Redis（热缓存，近7天）    │  │  - 夏普比率、最大回撤            │ │
│  │  文件缓存（Parquet格式）   │  │  - Calmar比率、年化收益          │ │
│  └───────────────────────────┘  │  - Sortino比率、胜率              │ │
│                                  │  - Walk-Forward防过拟合          │ │
│                                  └──────────────────────────────────┘ │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                      任务队列（Redis Stream）                     │ │
│  │  backtest:tasks:inbox  ──▶  BacktestWorker  ──▶  backtest:results│ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. 接口定义

### 3.1### 3.1 BacktestRequest（输入）

```python
@dataclass
class BacktestRequest:
    strategy_id: str          # 策略ID
    symbol: str               # 交易标的
    timeframe: str            # K线周期 (1m/5m/1h/1d)
    start_date: datetime      # 回测开始时间
    end_date: datetime        # 回测结束时间
    initial_capital: float    # 初始资金（USDT）
    position_sizing: str      # 仓位模式 (kelly/fixed_ratio/fixed_size)
    commission_rate: float    # 手续费率
    slippage_bps: int         # 滑点（基点）
    parameters: dict          # 策略参数
```

### 3.2 BacktestResult（输出）

```python
@dataclass
class BacktestResult:
    task_id: str
    status: str               # pending/running/completed/failed
    metrics: BacktestMetrics
    equity_curve: list[dict]  # [{timestamp, equity, drawdown}]
    trade_log: list[TradeRecord]
    parameter_sensitivity: dict  # 参数敏感性分析
    created_at: datetime
    completed_at: datetime
```

### 3.3 BacktestMetrics

```python
@dataclass
class BacktestMetrics:
    total_return: float       # 总收益率
    annualized_return: float  # 年化收益率
    sharpe_ratio: float       # 夏普比率
    sortino_ratio: float      # 索提诺比率
    max_drawdown: float       # 最大回撤
    max_drawdown_duration: int  # 最大回撤持续时间（天）
    win_rate: float           # 胜率
    profit_factor: float      # 盈亏比
    avg_trade: float          # 平均每笔收益
    total_trades: int         # 总交易次数
    calmar_ratio: float       # 卡玛比率
```

---

## 4. 数据层架构

```
历史数据来源
    ├── 交易所API（实时拉取）
    │       └── CCXT统一接口 → 归一化为OHLCV格式
    ├── TimescaleDB（本地缓存）
    │       └── hypertable: market_data(symbol, timeframe, timestamp)
    └── CSV导入（离线数据）

数据质量检查
    ├── 缺口检测（连续性校验）
    ├── 异常值过滤（3σ原则）
    └── 除权复权处理（加密货币分叉）
```

### 4.1 TimescaleDB表结构

```sql
CREATE TABLE market_data (
    id          BIGSERIAL,
    symbol      VARCHAR(20)  NOT NULL,
    timeframe   VARCHAR(5)   NOT NULL,
    timestamp   TIMESTAMPTZ  NOT NULL,
    open        NUMERIC(20,8),
    high        NUMERIC(20,8),
    low         NUMERIC(20,8),
    close       NUMERIC(20,8),
    volume      NUMERIC(30,8),
    PRIMARY KEY (symbol, timeframe, timestamp)
);
SELECT create_hypertable('market_data','timestamp');
```

---

## 5. 回测引擎实现

### 5.1 事件驱动核心循环

```python
class BacktestEngine:
    async def run(self, request: BacktestRequest) -> BacktestResult:
        bars = await self._load_data(request)
        portfolio = Portfolio(initial_capital=request.initial_capital)
        strategy = await self._load_strategy(request.strategy_id)

        for bar in bars:
            # 1. 更新市场状态
            market_state = self._update_market(bar)
            # 2. 风控前置检查（与实盘保持一致）
            if not self._risk_precheck(portfolio, market_state):
                continue
            # 3. 生成信号
            signal = strategy.on_bar(market_state)
            # 4. 仓位计算
            order = self._size_position(signal, portfolio, request.position_sizing)
            # 5. 模拟成交（含滑点+手续费）
            fill = self._simulate_fill(order, bar, request)
            # 6. 更新持仓
            portfolio.update(fill)

        return self._calculate_metrics(portfolio)
```

### 5.2 蒙特卡洛模拟

```python
async def monte_carlo_simulation(
    trade_log: list[TradeRecord],
    n_simulations: int = 1000
) -> MonteCarloResult:
    # 对历史交易序列随机排列
    results = []
    for _ in range(n_simulations):
        shuffled = random.sample(trade_log, len(trade_log))
        equity = simulate_equity_curve(shuffled)
        results.append(calc_metrics(equity))
    return MonteCarloResult(
        max_drawdown_p95=np.percentile([r.max_drawdown for r in results], 95),
        ruin_probability=sum(1 for r in results if r.total_return < -0.5) / n_simulations
    )
```

---

## 6. 参数优化（网格搜索 + 遗传算法）

```python
class ParameterOptimizer:
    def grid_search(self, param_grid: dict, metric='sharpe_ratio') -> OptimizeResult:
        # Optuna + 并行回测
        study = optuna.create_study(direction='maximize')
        study.optimize(lambda trial: self._objective(trial, metric), n_trials=200)
        return OptimizeResult(best_params=study.best_params, best_value=study.best_value)

    def walk_forward(self, data, n_splits=5) -> WalkForwardResult:
        # 滚动窗口避免过拟合
        tscv = TimeSeriesSplit(n_splits=n_splits)
        results = []
        for train_idx, test_idx in tscv.split(data):
            params = self._optimize_on_train(data[train_idx])
            score = self._test_on_oos(data[test_idx], params)
            results.append({'params': params, 'oos_score': score})
        return WalkForwardResult(results)
```

---

## 7. 性能要求

| 场景 | 目标 |
|------|------|
| 单策略1年日线回测 | < 2秒 |
| 单策略1年小时线回测 | < 10秒 |
| 参数优化200次迭代 | < 5分钟 |
| 并发回测任务数 | 最多10个 |

---

## 8. 与Agent系统集成

- QuantEngineerAgent通过BacktestSkill提交任务
- 任务入队：Redis Stream `backtest:tasks`
- 结果通知：Redis PubSub `backtest:results:{task_id}`
- 结果持久化：PostgreSQL表 `backtest_results`
- 前端通过WebSocket订阅实时进度更新

---

*文档版本：v1 | 架构师B | 待第2轮专家评审*
