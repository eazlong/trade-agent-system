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
  → RiskModel[].filter() → PortfolioTarget[] (filtered)
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
        """将多个 Insight 转化为带权重的 PortfolioTarget 列表。

        context 必须提供:
        - positions: dict[str, Decimal] — 各 symbol 当前持仓
        - total_capital: Decimal — 总可用资金
        - current_prices: dict[str, Decimal] — 各 symbol 当前价格
        """
        ...
```

`target_weight` 和 `target_quantity` 均为 `Decimal` 类型，避免 float/Decimal 混用导致 TypeError。

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
    """风控检查模型基类。链式执行，每个模型过滤不满足条件的 target。

    约定:
    - 方法命名为 `filter()`（非 `check()`），明确表达过滤语义
    - 不修改传入的 targets，返回新列表（不可变语义）
    - 单个模型抛异常时应能安全回滚（链上已生效的过滤无法回滚）
    """

    @abstractmethod
    def filter(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        """返回通过风控过滤的 targets 子集。不修改传入列表。"""
        ...
```

实现（四个模型链式执行，注意排序：PositionSize → TotalExposure → MaxPositions → StopLoss）：

| 模型 | 检查规则 |
|------|----------|
| `PositionSizeRisk` | 单标的仓位占总资金比例 ≤ 配置上限（默认 20%） |
| `TotalExposureRisk` | 所有持仓总价值 ≤ 总资金 × 暴露上限（默认 80%） |
| `MaxPositionsRisk` | 同时持仓标的总数 ≤ 配置上限（默认 5） |
| `StopLossRisk` | 当前浮亏 ≥ 阈值时，插入平仓 target 并置顶优先级 |

风险模型通过 `PortfolioTarget` 的 `metadata` 字段传递风险标注信息（如 `{"rejected_by": "PositionSizeRisk", "reason": "仓位超限"}`），不改变现有数据结构。

**链式执行顺序**：`PositionSizeRisk → TotalExposureRisk → MaxPositionsRisk → StopLossRisk`。排序原则：先单标的限制（PositionSize），再全局限制（TotalExposure/MaxPositions），最后止损（StopLoss 需要完整过滤后的 targets）。

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

    def apply_risk_filters(
        self, targets: list[PortfolioTarget], context: StrategyContext
    ) -> list[PortfolioTarget]:
        """链式执行所有风险模型，每个模型过滤不满足条件的 target。"""
        for rm in self.risk_models:
            targets = rm.filter(targets, context)
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
safe_targets = strategy.apply_risk_filters(targets, strategy.ctx) # ④ 新增
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
| `strategy_engine/base.py` | **修改** | BaseStrategy 新增 `universe`/`portfolio`/`risk_models` 属性 + `select_universe()`/`apply_risk_filters()` 方法 |
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
   - `PositionSizeRisk.filter()` 正确拒绝超限单标的
   - `TotalExposureRisk.filter()` 正确计算总暴露
   - `MaxPositionsRisk.filter()` 正确限制持仓数
   - `StopLossRisk.filter()` 正确插入止损 target
   - 链式执行：多个 Risk 模型串联，前置过滤结果传入后置
   - 全部通过时返回完整列表
   - 不修改传入的 targets 列表（不可变语义验证）
   - 空 targets 列表输入时不抛异常

4. **集成测试**：
   - 老策略（RsiCrossStrategy）通过 Phase 2 管线回测，结果与 Phase 1 完全一致
   - 多标的策略通过新管线正常回测
   - Universe 模型切换不影响回测结果确定性

5. **向后兼容测试**：
   - 未设置 universe/portfolio/risk_models 的策略行为不变
   - `on_bar()` 返回 OrderSignal 的转换链路不变
   - `generate_insights()` 默认调用 `on_bar()` 的行为不变

6. **Phase 1 Bug 修复验证**（在 Phase 2 开始前修复）：
   - **C3 卖出信号**：`direction=sell` 的 Insight 经过 `construct_portfolio()` → `_target_to_order()` 链路能正确生成卖出 order（`target_quantity=0` → `diff = -position`）
   - **C4 Position 跟踪**：实盘模式下 `ctx.position` 在每根 K 线处理前从交易所同步，处理后正确更新
   - 修复后 Phase 1 全套测试仍然通过

## 审核发现（2026-05-07 四 Agent 并行审核）

以下问题来自 architect、code-reviewer、security-reviewer、silent-failure-hunter 四名 Agent 的独立审核。

### CRITICAL（必须修复后再实现 Phase 2）

| # | 问题 | 来源 |
|---|------|------|
| **C1** | **`StrategyContext` 是单标的** — `ctx.symbol: str`、`ctx.position: Decimal` 无法支撑多标的。Phase 2 需要扩展为 `positions: dict[str, Decimal]` 或新建 `SymbolContext` 结构 | Architect, Code Review |
| **C2** | **管线 K 线数据缺口** — `generate_insights(kline, history)` 只接收一根 K 线，但多标的场景需要 `dict[str, Kline]`。引擎必须知道如何为 universe 返回的每个 symbol 拉取数据 | Architect |
| **C3** | **卖出信号被静默丢弃（Phase 1 bug）** — `construct_portfolio()` 卖出时将 `target_quantity` 设为 `context.position`，随后 `_target_to_order()` 计算 `diff = position - position = 0`，返回 `None`。卖出订单永远不会发出 | Silent Failure |
| **C4** | **实盘 `ctx.position` 永不更新（Phase 1 bug）** — `LiveStrategyRunner` 中 position 初始化为 `Decimal("0")` 后从未从交易所同步，导致每根 K 线都生成全量买入订单 | Silent Failure |
| **C5** | **Float/Decimal 类型混用** — Portfolio 模型用 `1.0 / len(insights)` 产生 float，但 `PortfolioTarget.target_quantity` 是 `Decimal`，导致 `TypeError` | Silent Failure |
| **C6** | **`risk_models=[]` 默认值存在安全隐患** — 新策略默认无任何风控，一旦策略开发者忘记显式设置就直接裸跑。建议最少默认加载 `PositionSizeRisk`，或要求显式传入 `risk_models` 参数 | Security |
| **C7** | **TOCTOU 竞态条件** — 多标的按顺序逐一调用 `_target_to_order()` 下单。在第一个和最后一个 order 之间市场价格可能变动，导致风控检查（基于下单前的状态）实际失效 | Security |

关于 C3/C4 的详细分析：

**C3 卖出信号丢失根因**：Phase 1 的 `construct_portfolio()` 对卖出方向做了特殊处理 — 将 `target_quantity` 设为 `context.position`（当前持仓量），意图通过 `_target_to_order()` 的 `diff = target_quantity - ctx.position` 计算出卖出量。但当 `target_quantity == ctx.position` 时，`diff == 0`，`_target_to_order()` 返回 `None`，卖出信号被丢弃。

修复方向：`construct_portfolio()` 在卖出时应设置 `target_quantity = Decimal("0")`，让 `diff = 0 - position = -position`，生成正确卖出量。或引入独立的 `action` 字段区分买入/卖出/平仓。

**C4 Position 跟踪缺失根因**：`LiveStrategyRunner` 在 `runner.py:191` 将 `ctx.position` 初始化为 `Decimal("0")`，之后未从交易所/数据库同步实际持仓。回测模式下 position 通过成交记录累计更新，但实盘模式缺少这个逻辑。

修复方向：在 `on_kline()` 处理前调用交易所 REST API 查询当前持仓，或从 `Position` 模型 DB 记录中读取。同时修复 `runner.py` 中每次处理完订单后更新 `ctx.position`。

### HIGH（建议在实现前解决）

| # | 问题 | 来源 |
|---|------|------|
| **H1** | **`allocate()` 接口定义缺少 context** — 设计文档中 `allocate(insights, context)` 已包含 context，但需要明确 context 提供持有仓位、可用余额等信息以计算实际分配量 | Architect, Code Review |
| **H2** | **`check()` 语义不明确** — 命名暗示只读检查，但实际执行过滤+修改。建议统一为 `filter()` 并明确返回值是不可变副本 | Architect, Code Review |
| **H3** | **`construct_portfolio()` 与 `allocate()` 职责重叠** — 两者都产出 `PortfolioTarget`。歧义在于"谁负责生成卖出信号"：当某标的持仓需要降低时，是由 Portfolio 模型的 rebalance 逻辑处理，还是由 Risk 模型插入平仓 target | Code Review |
| **H4** | **Volume/Kelly 模型的外部数据源未声明** — `VolumeTopUniverse` 需要 24h 成交量数据，`KellyPortfolio` 需要历史胜率。这些数据的获取接口（通过 context 的方法？独立 data provider？）未在设计文档中定义 | Code Review |
| **H5** | **`_target_to_order()` 在回测和实盘中重复实现** — `backtest_mode.py:111-121` 和 `live_mode.py:226-236` 逻辑相同但独立维护，存在 drift 风险。应考虑提取到公共模块 | Architect |
| **H6** | **Risk 模型链式执行中异常处理不一致** — `apply_risk_filters()` 中某个 Risk 模型的 `filter()` 抛异常时，之前模型的过滤已生效且无法回滚，后续模型被跳过。需要统一定义异常处理策略 | Silent Failure |

### MEDIUM（可在实现过程中处理）

| # | 问题 | 来源 |
|---|------|------|
| M1 | `StopLossRisk` 需要当前未实现盈亏（PnL），这超出了 `PortfolioTarget` 携带的信息范围 | Silent Failure |
| M2 | `SingleAssetPortfolio` 过滤 `direction=hold` 的 Insight，但其他 Portfolio 模型对此行为未定义 | Code Review |
| M3 | `HybridUniverse` 的空候选池降级策略未定义（返回空列表？fallback 到固定列表？） | Code Review |
| M4 | Universe 模型的 `select()` 是同步方法，但 `VolumeTopUniverse` 可能需要异步获取 24h 成交量数据 | Architect |
| M5 | `PortfolioTarget.metadata` 用于风控标注，但 key 命名未约定（如 `rejected_by`、`reason` 等） | Silent Failure |
| M6 | risk_models 的顺序依赖未文档化 — `StopLossRisk` 在 `PositionSizeRisk` 之前/之后会导致不同结果 | Security |
| M7 | `generate_insights()` 多标的调用约定 — 当 universe 返回多个 symbol 时，引擎是对每个 symbol 分别调用还是批量调用一次？ | Architect |
| M8 | Risk 模型实现时应注意不修改传入的 `PortfolioTarget` 对象（不可变语义），否则链式执行之间会产生副作用 | Architect |

### 实现前必须解决的事项

1. **Phase 1 bug 修复** — C3（卖出信号丢失）和 C4（position 跟踪缺失）必须在 Phase 2 开始前修复
2. **StrategyContext 重构** — 支持 `positions: dict[str, Decimal]` 的多标的结构（C1）
3. **数据供给接口** — 定义 K 线数据如何按 universe 结果分发给 `generate_insights()`（C2）
4. **数值类型统一** — 所有数值计算使用 Decimal（C5）
5. **risk_models 默认值** — 至少包含 `PositionSizeRisk`（C6）
6. **TOCTOU 缓解** — 对风控过滤后的 target 批量下单或在锁定状态下执行（C7）

## 风险与回滚

- **风险低**：不改变 Phase 1 数据类、不改变 DB schema、不改变 Redis Stream 格式
- **默认行为不变**：所有新属性都有默认值，老策略不感知
- **回滚方式**：`git revert` 即可
- **与 RiskGuard 关系**：Phase 2 的 Risk 模型是策略级别的内嵌风控（仓位大小、止损），RiskGuard 是独立进程级别的全局风控（熔断、硬限制），两者互补而非替代
