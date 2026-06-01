# Watch Signals 自动推导实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 create-strategy 技能的阶段3中嵌入 watch signals 推导逻辑，让 agent 自动生成 get_watch_signals 实现

**Architecture:** 修改 SKILL.md 文件的四个位置：拆分阶段3为子步骤、插入推导规则章节、增强模板注释、增加代码质量要求

**Tech Stack:** Markdown 技能文档编辑

---

## 文件结构

**修改文件：**
- `skills/create-strategy/SKILL.md` — 技能文档，包含工作流程、推导规则、模板示例

**修改位置：**
1. 第85-125行：阶段3章节（拆分为3个子步骤）
2. 第125行后：插入新章节"Watch Signals 推导规则"
3. 第258-283行：get_watch_signals 模板（增加注释说明）
4. 第126行后的代码质量要求章节（增加一条要求）

---

### Task 1: 拆分阶段3为推导子步骤

**Files:**
- Modify: `skills/create-strategy/SKILL.md:85-125`

- [ ] **Step 1: 定位阶段3章节**

当前内容（第85-103行）：
```markdown
### 阶段 3：生成策略代码

确认后，生成策略 Python 文件。

**输出路径**：`~/.tradelogx/strategies/{strategy_name}.py`

调用 `write_file` 工具：
```
write_file(
    file_path="~/.tradelogx/strategies/{strategy_name}.py",
    agent_name="quant",
    content="<策略代码>"
)
```

策略创建成功后，告知用户：
- strategy_name（供回测使用）
- 文件路径
```

- [ ] **Step 2: 替换为新的子步骤结构**

替换第85-103行内容为：
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
3. **映射格式** — 转换为标准 watch signal 格式：
   ```python
   {
       "interval": "1h",
       "indicator_type": "rsi",
       "indicator_params": {"period": 14},
       "condition": {"operator": "lt", "left": {"field": "rsi"}, "right": {"value": 30}},
       "trigger_type": "continuous"
   }
   ```

#### 3.3 生成完整代码

生成包含以下部分的策略代码：
- `on_bar` 实现
- `get_watch_signals` 实现（基于 3.2 推导结果）
- `on_start`、`on_stop` 等生命周期方法

**输出路径**：`~/.tradelogx/strategies/{strategy_name}.py`

调用 `write_file` 工具：
```
write_file(
    file_path="~/.tradelogx/strategies/{strategy_name}.py",
    agent_name="quant",
    content="<策略代码>"
)
```

策略创建成功后，告知用户：
- strategy_name（供回测使用）
- 文件路径
```

- [ ] **Step 3: 验证修改**

检查替换后的内容：
- 包含3个子步骤标题（3.1、3.2、3.3）
- 每个子步骤有清晰的说明和示例
- 保留了原有的输出路径和 write_file 调用说明

- [ ] **Step 4: Commit**

```bash
git add skills/create-strategy/SKILL.md
git commit -m "feat(skill): add watch signals derivation steps in phase 3"
```

---

### Task 2: 插入 Watch Signals 推导规则章节

**Files:**
- Modify: `skills/create-strategy/SKILL.md:125`（在第125行后插入）

- [ ] **Step 1: 定位插入位置**

当前第125行是：
```markdown
测试通过后，告知用户：
- strategy_name（供回测使用）
- 文件路径
- 模拟运行统计（信号数、买卖次数、模拟收益率）
```

下一行是：
```markdown
## 代码质量要求
```

需要在第125行后、"## 代码质量要求"前插入新章节。

- [ ] **Step 2: 插入推导规则章节**

在第125行后插入以下内容：
```markdown

## Watch Signals 推导规则

Agent 在阶段3生成代码时，需要将策略入场条件推导为 watch signals。以下是推导规则：

### 1. 简化原则

**只保留单指标条件**，复合条件在 `on_bar()` 中完整验证：

| 入场条件 | watch signal | on_bar 验证 |
|---------|-------------|------------|
| RSI < 30 且 EMA向上 | RSI < 30 | RSI < 30 && EMA_slope > 0 |
| 价格突破布林带上轨 且 MACD金叉 | 价格突破布林带上轨 | price > bb_upper && macd > signal |
| EMA_fast 上穿 EMA_slow | EMA_fast 上穿 EMA_slow | 完整条件（单指标已足够） |

### 2. 指标类型映射

将策略中使用的指标映射为 `indicator_type`：

| 策略指标 | indicator_type | indicator_params |
|---------|---------------|-----------------|
| RSI | `"rsi"` | `{"period": 14}` |
| EMA | `"ema"` | `{"period": 20}` |
| 布林带 | `"bollinger"` | `{"period": 20, "std_dev": 2.0}` |
| MACD | `"macd"` | `{"fast": 12, "slow": 26, "signal_period": 9}` |
| 唐奇安通道 | `"donchian"` | `{"period": 20}` |
| ATR | `"atr"` | `{"period": 14}` |

### 3. 条件运算符映射

将入场条件转换为 `condition` 格式：

| 条件描述 | operator | left | right |
|---------|---------|------|-------|
| RSI < 30 | `"lt"` | `{"field": "rsi"}` | `{"value": 30}` |
| RSI > 70 | `"gt"` | `{"field": "rsi"}` | `{"value": 70}` |
| 价格上穿布林带上轨 | `"cross_above"` | `{"field": "price"}` | `{"field": "upper"}` |
| EMA 下穿 EMA_slow | `"cross_below"` | `{"field": "ema"}` | `{"field": "ema_slow"}` |
| 价格 > 50000 | `"gt"` | `{"field": "price"}` | `{"value": 50000}` |

### 4. 周期选择规则

使用策略的**最小K线周期**作为 `interval`：

- 策略使用 4h + 1d → watch signal 使用 `"4h"`
- 策略仅使用 1h → watch signal 使用 `"1h"`
- 多时间框架策略 → 使用最小周期（如 15m + 1h → `"15m"`）

### 5. trigger_type 选择

- **持续信号**（如 RSI < 30）→ `"continuous"`
- **突破信号**（如 价格上穿布林带）→ `"once"`（首次触发后移除）

### 6. 多条件策略示例

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

```

- [ ] **Step 3: 验证插入位置**

检查：
- 新章节位于"## 代码质量要求"之前
- 包含6个子章节（简化原则、指标映射、运算符映射、周期选择、trigger_type、示例）
- 示例代码格式正确

- [ ] **Step 4: Commit**

```bash
git add skills/create-strategy/SKILL.md
git commit -m "feat(skill): add Watch Signals Derivation Rules section"
```

---

### Task 3: 增强 get_watch_signals 模板注释

**Files:**
- Modify: `skills/create-strategy/SKILL.md:258-283`（get_watch_signals 模板）

- [ ] **Step 1: 定位模板位置**

当前第258-283行是：
```python
    def get_watch_signals(self) -> list[dict]:
        """返回最小周期信号配置，由 LiveStrategyRunner 注册到 SignalMonitor 做初筛。

        每个 dict 包含:
            interval: str         — K线周期 (如 "15m", "1h")
            indicator_type: str   — 指标类型 (donchian/bollinger/rsi/price_watch/...)
            indicator_params: dict — 指标参数 (如 {"period": 20})
            condition: dict       — 触发条件，格式见下方说明
            trigger_type: str     — "once"(单次) 或 "continuous"(持续)

        默认返回 []。子类覆盖此方法启用两阶段信号检测：
        1. SignalMonitor 按最小周期检测单指标条件（轻量初筛）
        2. 触发后运行完整 on_bar() 验证多指标组合条件

        条件格式示例:
            # 价格上穿唐奇安通道上轨
            {"operator": "cross_above", "left": {"field": "price"},
             "right": {"field": "upper"}}
            # 价格大于指定值
            {"operator": "gt", "left": {"field": "price"},
             "right": {"value": 50000}}
            # RSI 小于阈值
            {"operator": "lt", "left": {"field": "rsi"},
             "right": {"value": 30}}
        """
        return []
```

- [ ] **Step 2: 在 docstring 开头增加说明**

在第259行的 docstring 开头增加：
```python
    def get_watch_signals(self) -> list[dict]:
        """返回最小周期信号配置，由 LiveStrategyRunner 注册到 SignalMonitor 做初筛。

        **重要**：此方法由 agent 在阶段3根据策略入场条件自动推导生成，
        不需要用户手动配置。推导规则见上方"Watch Signals 推导规则"章节。

        示例（RSI超卖策略）：
        return [
            {
                "interval": "1h",
                "indicator_type": "rsi",
                "indicator_params": {"period": 14},
                "condition": {"operator": "lt", "left": {"field": "rsi"}, "right": {"value": 30}},
                "trigger_type": "continuous",
            }
        ]

        每个 dict 包含:
            interval: str         — K线周期 (如 "15m", "1h")
            indicator_type: str   — 指标类型 (donchian/bollinger/rsi/price_watch/...)
            indicator_params: dict — 指标参数 (如 {"period": 20})
            condition: dict       — 触发条件，格式见下方说明
            trigger_type: str     — "once"(单次) 或 "continuous"(持续)

        默认返回 []。子类覆盖此方法启用两阶段信号检测：
        1. SignalMonitor 按最小周期检测单指标条件（轻量初筛）
        2. 触发后运行完整 on_bar() 验证多指标组合条件

        条件格式示例:
            # 价格上穿唐奇安通道上轨
            {"operator": "cross_above", "left": {"field": "price"},
             "right": {"field": "upper"}}
            # 价格大于指定值
            {"operator": "gt", "left": {"field": "price"},
             "right": {"value": 50000}}
            # RSI 小于阈值
            {"operator": "lt", "left": {"field": "rsi"},
             "right": {"value": 30}}
        """
        return []  # agent 将根据推导规则填充实际内容
```

- [ ] **Step 3: 验证修改**

检查：
- docstring 开头增加了"重要"说明
- 包含了 RSI 超卖策略的完整示例
- `return []` 行增加了注释说明

- [ ] **Step 4: Commit**

```bash
git add skills/create-strategy/SKILL.md
git commit -m "feat(skill): enhance get_watch_signals template with derivation guidance"
```

---

### Task 4: 增加代码质量要求

**Files:**
- Modify: `skills/create-strategy/SKILL.md:126`（代码质量要求章节开头）

- [ ] **Step 1: 定位代码质量要求章节**

当前第126行开始：
```markdown
## 代码质量要求

### on_bar 签名（最关键）
```

需要在"## 代码质量要求"章节中增加一条新要求。

- [ ] **Step 2: 在章节末尾增加新要求**

定位到代码质量要求章节末尾（约第393行），在现有要求列表后增加：

```markdown
- **`get_watch_signals` 必须返回正确格式** — list[dict]，每个 dict 必须包含 interval、indicator_type、indicator_params、condition、trigger_type 五个字段；空策略返回 `[]`
```

- [ ] **Step 3: 验证新增内容**

检查：
- 新要求位于代码质量要求列表中
- 明确指定了必需字段
- 包含空策略的返回值说明

- [ ] **Step 4: Commit**

```bash
git add skills/create-strategy/SKILL.md
git commit -m "feat(skill): add get_watch_signals format requirement to code quality checklist"
```

---

## Spec Coverage 自检

**设计文档要求 → 对应任务：**

1. ✅ 阶段3拆分为子步骤 → Task 1
2. ✅ 推导规则章节 → Task 2
3. ✅ 模板注释增强 → Task 3
4. ✅ 代码质量要求 → Task 4

**无遗漏需求。**

---

## Placeholder 自检

**扫描计划：**
- ✅ 无 "TBD"、"TODO"、"implement later"
- ✅ 无 "Add appropriate error handling" 等模糊指令
- ✅ 所有代码步骤包含完整代码示例
- ✅ 所有步骤包含验证方法
- ✅ 无"Similar to Task N"引用

**无占位符问题。**

---

## Type Consistency 自检

**类型一致性：**
- ✅ Task 2 中的 watch signal 格式与 Task 3 模板示例一致
- ✅ 所有示例使用相同的字段名（interval、indicator_type、indicator_params、condition、trigger_type）
- ✅ 运算符名称一致（lt、gt、cross_above、cross_below）

**无类型不一致问题。**

---

Plan complete. Ready for execution.