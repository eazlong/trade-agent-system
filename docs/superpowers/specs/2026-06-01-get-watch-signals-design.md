# Watch Signals 自动推导设计

## 背景

`create-strategy` 技能当前问题：
- SKILL.md 中已包含 `get_watch_signals()` 方法的完整文档（第258-353行）
- 文档包含返回值格式、运算符映射、示例代码
- 但生成的策略代码中缺少 `get_watch_signals` 的实际实现
- Agent 不知道何时、如何推导并生成 watch signals

## 问题

用户期望：
- Agent 根据策略入场条件自动推导出正确的 watch signals
- 不需要用户手动配置或理解技术细节
- 在工作流中明确推导步骤，指导 agent 执行

## 解决方案

在 SKILL.md 的 **阶段3：生成策略代码** 中嵌入 watch signals 推导逻辑，拆分为3个子步骤：

1. **分析入场条件** — 提取主要指标、触发条件、K线周期
2. **推导 watch signals** — 简化条件、映射格式、选择周期
3. **生成完整代码** — 包含 `get_watch_signals` 实现

## 实现细节

### 1. 阶段3推导子步骤

在 SKILL.md 第85-125行，将原有的"生成策略代码"单一步骤拆分为：

```markdown
### 阶段 3：生成策略代码

确认后，按以下步骤生成策略 Python 文件：

#### 3.1 分析入场条件

从用户确认的需求中提取：
- **主要指标**：如 RSI、MACD、布林带、EMA 等
- **触发条件**：如"RSI < 30"、"价格突破布林带上轨"、"EMA 上穿 EMA_slow"
- **K线周期**：如 1h、4h、15m

#### 3.2 推导 watch signals

根据推导规则（见下方"Watch Signals 推导规则"章节），将入场条件映射为最小周期信号：

1. **简化条件** — 复合条件拆解为单指标条件（如"EMA向上 且 RSI<30" → 只取"RSI<30"）
2. **选择周期** — 使用策略的最小K线周期作为 interval
3. **映射格式** — 转换为标准 watch signal 格式

#### 3.3 生成完整代码

生成包含以下部分的策略代码：
- `on_bar` 实现
- `get_watch_signals` 实现（基于 3.2 推导结果）
- `on_start`、`on_stop` 等生命周期方法
```

### 2. 推导规则章节

在 SKILL.md 第125行后（"工作流程"章节结束后）增加新章节：

```markdown
## Watch Signals 推导规则

Agent 在阶段3生成代码时，需要将策略入场条件推导为 watch signals。

### 1. 简化原则

只保留单指标条件，复合条件在 `on_bar()` 中完整验证。

### 2. 指标类型映射

将策略中使用的指标映射为 `indicator_type`：
- RSI → `"rsi"`
- EMA → `"ema"`
- 布林带 → `"bollinger"`
- MACD → `"macd"`
- 唐奇安通道 → `"donchian"`

### 3. 条件运算符映射

将入场条件转换为 `condition` 格式：
- RSI < 30 → `"operator": "lt"`
- 价格上穿布林带上轨 → `"operator": "cross_above"`

### 4. 周期选择规则

使用策略的最小K线周期作为 `interval`。

### 5. trigger_type 选择

- 持续信号（RSI < 30）→ `"continuous"`
- 突破信号（价格上穿）→ `"once"`

### 6. 多条件策略示例

提供完整的推导过程示例（4h EMA + 15m Donchian 策略）。
```

### 3. 文件模板增强

在 SKILL.md 第259行（`get_watch_signals` 模板位置）增加注释：

```python
def get_watch_signals(self) -> list[dict]:
    """返回最小周期信号配置。

    **重要**：此方法由 agent 在阶段3根据策略入场条件自动推导生成，
    不需要用户手动配置。推导规则见上方"Watch Signals 推导规则"章节。

    示例（RSI超卖策略）：
    return [{
        "interval": "1h",
        "indicator_type": "rsi",
        ...
    }]
    """
    return []  # agent 将根据推导规则填充实际内容
```

### 4. 代码质量要求增强

在 SKILL.md 第381-393行增加一条要求：

```markdown
- **`get_watch_signals` 必须返回正确格式** — list[dict]，每个 dict 必须包含
  interval、indicator_type、indicator_params、condition、trigger_type 五个字段
```

## 影响范围

- **修改文件**：`skills/create-strategy/SKILL.md`（仅此一个文件）
- **不影响其他文件**：不修改策略引擎、测试工具、其他技能文件

## 潜在问题与处理

### 1. 推导错误

- `test_strategy` 工具验证格式（必需字段检查）
- 用户可在实盘部署后修改策略文件调整

### 2. 复合条件简化歧义

- 推导规则中增加优先级说明（明确触发 > 趋势，最小周期优先）
- Agent 在推导时说明简化理由

### 3. 用户不理解 watch signals

- 阶段2确认需求时不展示（避免困惑）
- 生成代码后告知用户包含自动生成的预筛选配置

### 4. 多时间框架策略

- 推导规则明确：使用最小周期
- Agent 推导时说明周期选择

## 测试验证

- **阶段4测试**：`test_strategy` 工具验证 `get_watch_signals` 返回格式正确
- **实盘验证**：用户部署实盘后观察 watch signals 是否触发

## 部署步骤

1. 编辑 `skills/create-strategy/SKILL.md`
2. 提交改动到 git
3. 无需重启服务（技能文件运行时加载）

## 成功标准

- Agent 在生成策略代码时自动包含 `get_watch_signals` 实现
- 推导的 watch signals 格式正确（包含所有必需字段）
- 用户无需理解技术细节即可得到完整可用的实盘策略代码