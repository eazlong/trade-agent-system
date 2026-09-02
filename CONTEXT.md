# Context: Agent System Data Access Tools

> **维护约定**：新增的领域概念必须同步加入本文件的 Glossary；被否决的设计分叉放入 Resolved Decisions，避免未来重提。

## Glossary

| Term | Definition |
|------|-----------|
| **MemoryEntry** | 召回结果的通用货币数据类：`source / content / ts / metadata / score`。`source` 取值 `"working.private" / "working.shared" / "longterm"`。`WorkingMemory.recall` 与 `LongTermMemory.search` 返回同构数据，facade 才能去重排序 |
| **ConversationLog** | user 级的对话历史 seam。接口：`append(entries, keep_turns) / get(max_turns) / clear()`。与 agent 无关，因为对话属于 user。实现 `RedisConversationLog`，可选注入 `Compressor` 协议做自动压缩 |
| **Compressor** | 协议：`async def compress(old_turns) → str`。生产实现 `LLMCompressor` 包 `LLMClient`，测试用 `NoopCompressor`。把"LLM 压缩对话"这个跨层依赖从 memory 模块里抽出来 |
| **WorkingMemory** | per-(user, agent) 的短期工作记忆 seam。接口：`remember(content, scope, memory_type, importance) / recall(query, top_k)`。`scope` 三态：`"private" / "shared" / "both"`，默认 `"both"`（与旧 `write_l2(shared=True)` 双写语义一致） |
| **LongTermMemory** | per-(user, agent) 的长期知识 seam。接口：`store(content, metadata, memory_type, importance) / search(query, top_k)`。实现 `PgvectorLongTerm`（依赖 `AgentMemory` 模型） |
| **MemoryFacade** | 组合 `ConversationLog + WorkingMemory + LongTermMemory` 的薄层。给 supervisor 用。提供 `build_context(query)`（recall+search 去重截断）、`remember(content, persist_to, ...)`（按 `persist_to ∈ {working, longterm, both}` 写对应层）、以及 `append_conv / get_conv / clear_conv` 透传 |
| **MemoryManager** | 保留名字的工厂（向后兼容入口）。构造参数 `(agent_type, user_id, agent_name)`，返回的对象实质是 `MemoryFacade`，通过 `__getattr__` 转发旧方法名（`retrieve → build_context`、`write_l2 → remember(persist_to="working")` 等），调用方零改动 |
| **内部数据工具** | Agent 查询系统内部业务数据的 BaseTool，遵循 user_id 自动过滤和敏感字段脱敏原则 |
| **user_id 自动过滤** | 工具层从执行上下文自动获取 user_id，查询时过滤为 `request.user` 或 `tracker_context.user_id`，Agent 无需显式传递 |
| **敏感字段脱敏** | 交易所 API Key/Secret 加密存储，工具返回时只暴露 label/exchange/is_active，不返回明文密钥 |
| **notify_user** | Agent 主动通知用户的工具，从当前执行上下文自动获取 user_id |
| **通知渠道** | 通过 channel_resolver 解析用户活跃的 Telegram 或 Lark 渠道 |
| **通知消息** | 统一格式的普通文本消息，不做 urgency 分级 |
| **上下文 user_id** | 从 AgentMessage.user_id 或 tracker_context 中获取，Agent 不需要显式传递 |
| **水平关键价位** | 由一组 K 线的局部高点或局部低点聚类得到的近似水平价格区域。它不是永久的“支撑”或“压力”；当给定当前价时，低于当前价的水平关键价位可称为支撑位，高于当前价的水平关键价位可称为压力位 |
| **触碰** | 一段 K 线价格进入某个水平关键价位动态容差区间的事件。连续多根 K 线命中同一容差区间属于同一个触碰簇，只计为一次触碰 |
| **触碰簇** | 多根相邻或间隔不足最小间隔的 K 线连续命中同一水平关键价位容差区间形成的集合。触碰簇用于避免把横盘贴线误算为多次有效触碰 |
| **箱体**（Box Range） | 价格在指定时间区间内反复触及上下边界所形成的震荡区域；边界由 ≥2 个局部高/低 pivot 聚合而成且被 K 线触碰 ≥2 次 |
| **上下边界聚合**（Boundary Clustering） | 将窗口内多个局部高/低 pivot 通过容差带聚类形成单一上/下边界价格的过程；算法复用 `horizontal_key_levels` 的 cluster 逻辑 |
| **两次验证**（Dual Verification） | 箱体成立的必要条件——上下边界同时满足 `pivot_count ≥ min_pivots` 且 `touches ≥ min_touches`；单一条件成立不足以认定箱体 |
| **箱体判定**（Box Range Detection） | 给定 K 线、宽度阈值与验证门槛，判断市场是否处于箱体震荡并返回上下边界的处理 |

## Resolved Decisions

### 内部数据工具设计

- **首批工具范围**：ListBacktestsTool、ListOrdersTool、ListExchangeAccountsTool
- **user_id 过滤**：工具层强制过滤为当前用户，Agent 参数中不暴露 user_id
- **user_id 来源**：复用现有架构，WebSocket 消息 fields.user_id → AgentMessage.user_id → Agent._current_user_id → 自动注入 tc.arguments["user_id"]
- **ExchangeAccount 脱敏**：返回 label/exchange/is_active/testnet，不暴露 api_key_enc/api_secret_enc
- **返回字段精简策略**：
  - BacktestResult：返回基本指标（sharpe/win_rate/drawdown等），不返回大数据 JSONField（equity_curve/drawdown_curve/ohlcv_data/indicator_data），引导用户去前端查看详情
  - Order：返回交易核心字段（symbol/side/status/filled_quantity/realized_pnl），提供 live_session_id 外键引导查看详情
- **ID 参数**：支持模糊匹配开头（startswith），例如 `backtest_id="a1b2"` 匹配所有以 "a1b2" 开头的回测
- **过滤参数**：
  - ListBacktestsTool：backtest_id（startswith）、symbol、strategy_name（icontains）、review_status、limit（默认 20，上限 100）
  - ListOrdersTool：order_id（startswith）、symbol、side、status、limit（默认 50，上限 200）
  - ListExchangeAccountsTool：account_id（startswith）、limit（默认 10，上限 50）
- **返回格式统一**：`{"total": int, "returned": int, "has_more": bool, "results": [...]}`，Agent 可通过 has_more 判断是否引导用户去前端
- **工具 description**：明确过滤能力、返回内容（特别是脱敏）、使用场景（Agent 何时调用）
- **异步数据库查询**：独立辅助函数 + `@sync_to_async` 装饰器 + `close_old_connections()`，复用 signal_monitor.py 的模式
- **工具注册**：在 `apps/agent/tools/__init__.py` 中注册到 ToolRegistry
- **字段类型转换**：UUID → 字符串（完整）、Decimal → float、datetime → ISO 8601、外键 → 关联对象名称（strategy.name）或完整 ID（live_session_id）
- **过滤参数**：支持 symbol/strategy/status（Backtest）、symbol/side/status（Order）
- **排序**：默认按 created_at DESC
- **分页**：首批不分页，返回最近 N 条（N 待定），超出时引导用户去前端查看
- **关联数据**：BacktestTrade/GridSearchJob/LiveSession 不单独提供工具，通过外键 ID 引导用户查看详情

### 新建会话（New Session）

**定义**：用户主动发起的动作，清除当前会话的交互状态，从零开始新对话。

**清除范围：**
- SessionState（退出多轮/暂停/工作流状态）
- conv_history（对话历史归零）

**不清除：**
- L2/L3 长期记忆（跨会话的知识沉淀）
- Frame 状态（交易框架独立于对话生命周期）
