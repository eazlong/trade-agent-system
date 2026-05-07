# Phase 2: 多标的 Universe/Portfolio/Risk 模型设计

## 目标

在 Phase 1 的 Insight → PortfolioTarget 中间层基础上，新增 Universe Selection 和 Risk Management 模型，使策略管线支持多标的交易，对标 QuantConnect Lean 5 阶段框架的后三层。

## 背景

Phase 1 实现了：
- `Insight` — 策略洞察数据类
- `PortfolioTarget` — 目标持仓数据类（含 `target_weight` Phase 2 预留字段）
- `on_bar()` 改为具体方法（默认返回 None）
- `generate_insights()` + `construct_portfolio()` 默认实现
- 引擎调用链：`insights → targets → _target_to_order → _process_signal`

Phase 1 的限制：
- 只能交易 `context.symbol` 单个标的
- 没有标的筛选机制
- 没有风控过滤（完全依赖外部 RiskGuard）

## 架构决策

**选择方案 B：独立可插拔组件** — 三个模型基类各自独立，策略通过组合选用。

- **方案 A（增量扩展）被拒绝** — 所有逻辑堆在 BaseStrategy 中，文件膨胀，耦合高
- **方案 C（正式 Pipeline）被拒绝** — 过度设计，当前单用户规模不需要管线编排框架
- **方案 B 优势** — 每层独立测试、独立演进；策略通过组合选用模型；新增模型不改基类

## 目标数据流

```
Phase 1:
  on_bar → Insight[] → PortfolioTarget[] → OrderSignal → Engine

Phase 2:
  Universe.select() → Symbol[]
  → generate_insights() → Insight[]
  → PortfolioModel.allocate() → PortfolioTarget[]
  → RiskModel[].check() → PortfolioTarget[] (filtered)
  → _target_to_order() → OrderSignal → Engine
```

## 新增数据结构

无需新增数据结构。Phase 1 的 `Insight`、`PortfolioTarget`、`OrderSignal` 足以支撑 Phase 2。

`PortfolioTarget.target_weight` 在 Phase 2 中正式启用，用于 Portfolio 模型输出的权重分配。

## 模型基类设计

### BaseUniverseModel

```python
from abc import ABC, abstractmethod

class BaseUniverseModel(ABC):
    """标的筛选模型基类。"""

    @abstractmethod
    def select(self, context: "StrategyContext") -> list[str]:
        """返回可交易的 symbol 列表。"""
        ...
```

实现：

| 模型 | 逻辑 | 参数 |
|------|------|------|
| `FixedListUniverse` | 从配置/DB 读取白名单 | `symbols: list[str]` |
| `VolumeTopUniverse` | 按 24h 成交量排序取 Top-N | `top_n: int`, `min_volume_usdt: float`, `quote_asset: str` |
| `HybridUniverse` | 固定候选池 ∩ 动态过滤 → Top-N | `candidates: list[str]`, `min_volume_usdt: float`, `top_n: int` |

### BasePortfolioModel

```python
class BasePortfolioModel(ABC):
    """组合构建模型基类。"""

    @abstractmethod
    def allocate(
        self,
        insights: list["Insight"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        """将多个 Insight 转化为带权重的 PortfolioTarget 列表。"""
        ...
```

实现：

| 模型 | 逻辑 |
|------|------|
| `EqualWeightPortfolio` | N 个 Insight 各 1/N 资金，`target_weight = 1.0 / len(insights)` |
| `ConfidenceWeightedPortfolio` | 按 `confidence` 加权，`weight_i = conf_i / sum(conf)` |
| `KellyPortfolio` | Kelly 公式 `f* = (p*b - q) / b`（Phase 2 后期） |

默认模型 `SingleAssetPortfolio`：保持 Phase 1 行为，直接使用 Insight.quantity 或 fallback params。

### BaseRiskModel

```python
class BaseRiskModel(ABC):
    """风控检查模型基类。链式执行，每个模型过滤不满足条件的 target。"""

    @abstractmethod
    def check(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        """返回通过风控检查的 targets 子集。"""
        ...
```

实现（四个模型链式执行）：

| 模型 | 检查规则 |
|------|----------|
| `PositionSizeRisk` | 单标的仓位占总资金比例 ≤ 配置上限（默认 20%） |
| `TotalExposureRisk` | 所有持仓总价值 ≤ 总资金 × 暴露上限（默认 80%） |
| `MaxPositionsRisk` | 同时持仓标的总数 ≤ 配置上限（默认 5） |
| `StopLossRisk` | 当前浮亏 ≥ 阈值时，插入平仓 target 并置顶优先级 |

风险模型通过 `PortfolioTarget` 的 `metadata` 字段传递风险标注信息（如被过滤原因），不改变现有数据结构。

## BaseStrategy 变更

### 新增属性

```python
class BaseStrategy(ABC):
    # Phase 2 新增属性（__init__ 中设置默认值，保证向后兼容）
    universe: BaseUniverseModel      # 默认: FixedListUniverse([ctx.symbol])
    portfolio: BasePortfolioModel    # 默认: SingleAssetPortfolio()
    risk_models: list[BaseRiskModel] # 默认: []

    def __init__(self, context: StrategyContext):
        self.ctx = context
        self.universe = FixedListUniverse([context.symbol])
        self.portfolio = SingleAssetPortfolio()
        self.risk_models = []
```

### 新增方法

```python
    def select_universe(self) -> list[str]:
        """委托给 universe 模型，返回可交易 symbol 列表。"""
        return self.universe.select(self.ctx)

    def apply_risk_checks(
        self, targets: list[PortfolioTarget], context: StrategyContext
    ) -> list[PortfolioTarget]:
        """链式执行所有风险模型，每个模型过滤不满足条件的 target。"""
        for rm in self.risk_models:
            targets = rm.check(targets, context)
            if not targets:
                break
        return targets
```

### construct_portfolio 增强

`construct_portfolio()` 内部检测 `self.portfolio` 类型：
- 如果是 `SingleAssetPortfolio`（默认），使用现有 Phase 1 逻辑
- 如果被策略覆盖为自定义模型，委托给 `self.portfolio.allocate(insights, context)`

## 引擎变更

### BacktestEngine.run() + LiveStrategyRunner.on_kline()

统一改为 5 步管线：

```python
# Phase 1 (3 步):
insights = strategy.generate_insights(kline, history)
targets = strategy.construct_portfolio(insights, strategy.ctx)
for target in targets:
    signal = engine._target_to_order(target, strategy.ctx)
    if signal:
        engine._process_signal(signal, kline)

# Phase 2 (5 步):
symbols = strategy.select_universe()                          # ① 新增
insights = strategy.generate_insights(kline, history)         # ② 不变
targets = strategy.construct_portfolio(insights, strategy.ctx) # ③ 不变
safe_targets = strategy.apply_risk_checks(targets, strategy.ctx) # ④ 新增
for target in safe_targets:
    signal = engine._target_to_order(target, strategy.ctx)   # ⑤ 不变
```

## 向后兼容保证

1. **默认 universe** = `FixedListUniverse([ctx.symbol])`，老策略只交易原来那个标的
2. **默认 portfolio** = `SingleAssetPortfolio()`，等同于 Phase 1 的 `construct_portfolio()`
3. **默认 risk_models** = `[]`，无风控过滤，老行为不变
4. 老策略（如 `RsiCrossStrategy`）零改动，通过新管线运行结果与 Phase 1 一致

## 策略编写新模式

```python
# 模式 A（Phase 1 风格，继续有效）：覆盖 on_bar
class RsiCrossStrategy(BaseStrategy):
    name = "rsi_cross"
    def on_bar(self, kline, history):
        if signal_condition:
            return self.ctx.buy(quantity=0.01, signal_name="rsi_entry")
        return None

# 模式 B（Phase 2 新风格）：多标的 + 自定义模型
class MultiSymbolStrategy(BaseStrategy):
    name = "multi_momentum"

    def __init__(self, context):
        super().__init__(context)
        self.universe = HybridUniverse(
            candidates=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
            min_volume_usdt=10_000_000,
            top_n=3,
        )
        self.portfolio = ConfidenceWeightedPortfolio()
        self.risk_models = [
            PositionSizeRisk(max_pct=0.20),
            MaxPositionsRisk(max_positions=5),
            StopLossRisk(threshold_pct=0.05),
        ]

    def generate_insights(self, kline, history):
        insights = []
        for symbol in self.select_universe():
            if buy_condition(symbol, kline):
                insights.append(Insight(
                    symbol=symbol, direction="buy",
                    confidence=calc_confidence(symbol),
                    period="4h", source=self.name,
                ))
        return insights
```

## 涉及文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `strategy_engine/universe.py` | **新增** | `BaseUniverseModel` + `FixedListUniverse` + `VolumeTopUniverse` + `HybridUniverse` |
| `strategy_engine/portfolio.py` | **新增** | `BasePortfolioModel` + `SingleAssetPortfolio` + `EqualWeightPortfolio` + `ConfidenceWeightedPortfolio` |
| `strategy_engine/risk.py` | **新增** | `BaseRiskModel` + `PositionSizeRisk` + `TotalExposureRisk` + `MaxPositionsRisk` + `StopLossRisk` |
| `strategy_engine/tests/test_universe.py` | **新增** | Universe 模型单元测试 |
| `strategy_engine/tests/test_portfolio.py` | **新增** | Portfolio 模型单元测试 |
| `strategy_engine/tests/test_risk.py` | **新增** | Risk 模型单元测试 |
| `strategy_engine/base.py` | **修改** | BaseStrategy 新增 `universe`/`portfolio`/`risk_models` 属性 + `select_universe()`/`apply_risk_checks()` 方法 |
| `strategy_engine/__init__.py` | **修改** | 导出新模型类 |
| `strategy_engine/backtest_mode.py` | **修改** | `run()` 调用链改为 5 步管线 |
| `strategy_engine/live_mode.py` | **修改** | `on_kline()` 和 `_on_validate_trigger()` 调用链同步 |

以下文件**不需要改动**：
- `registry.py` — 注册机制不变
- `loader.py` — 加载机制不变
- `runner.py` — 传入参数不变
- `signals.py` — 仍然消费 `OrderSignal`
- `trading/executor.py` — 不感知上层变化
- `riskguard/guard.py` — Risk 模型是策略内嵌风控，与独立 RiskGuard 服务互补
- `signal_monitor/engine.py` — 不直接调用策略

## 测试计划

1. **Universe 模型单元测试**：
   - `FixedListUniverse` 返回正确白名单
   - `VolumeTopUniverse` 按成交量正确排序和筛选（mock 市场数据）
   - `HybridUniverse` 交集逻辑 + Top-N 截断
   - 空候选池返回空列表

2. **Portfolio 模型单元测试**：
   - `SingleAssetPortfolio` 行为与 Phase 1 `construct_portfolio()` 一致
   - `EqualWeightPortfolio` 权重正确分配
   - `ConfidenceWeightedPortfolio` 按 confidence 比例分配
   - 空 Insight 返回空 Target 列表
   - Insight direction=hold 不参与分配
   - `target_weight` 字段正确填充

3. **Risk 模型单元测试**：
   - `PositionSizeRisk` 正确拒绝超限单标的
   - `TotalExposureRisk` 正确计算总暴露
   - `MaxPositionsRisk` 正确限制持仓数
   - `StopLossRisk` 正确插入止损 target
   - 链式执行：多个 Risk 模型串联，前置过滤结果传入后置
   - 全部通过时返回完整列表

4. **集成测试**：
   - 老策略（RsiCrossStrategy）通过 Phase 2 管线回测，结果与 Phase 1 完全一致
   - 多标的策略通过新管线正常回测
   - Universe 模型切换不影响回测结果确定性

5. **向后兼容测试**：
   - 未设置 universe/portfolio/risk_models 的策略行为不变
   - `on_bar()` 返回 OrderSignal 的转换链路不变
   - `generate_insights()` 默认调用 `on_bar()` 的行为不变

## 风险与回滚

- **风险低**：不改变 Phase 1 数据类、不改变 DB schema、不改变 Redis Stream 格式
- **默认行为不变**：所有新属性都有默认值，老策略不感知
- **回滚方式**：`git revert` 即可
- **与 RiskGuard 关系**：Phase 2 的 Risk 模型是策略级别的内嵌风控（仓位大小、止损），RiskGuard 是独立进程级别的全局风控（熔断、硬限制），两者互补而非替代
