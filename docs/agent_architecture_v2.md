# Agent 通信架构 v2 设计文档

> 版本：v2
> 日期：2026-04-03
> 状态：待实现

## 1. 背景与目标

当前架构（v1）存在的问题：
- Supervisor 单点故障：意图解析、路由、会话管理三重职责集中于 Supervisor
- SubAgent 无法拒收：错误路由的消息被硬着头皮处理
- 跨 Agent 记忆隔离：Analyst 的分析结论 Coach 看不到
- 多意图丢失：用户一次输入包含多个意图时只取第一个
- 会话超时处理缺失：多轮对话中断后上下文丢失

本设计的目标：
- 提升系统鲁棒性（Supervisor 降级、多 Agent 拒收路由）
- 支持跨 Agent 上下文共享（关联任务记忆）
- 支持多意图串行处理
- 会话生命周期精细化管理（暂停/恢复/归档）

---

## 2. 通信架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                        用户消息                              │
└──────────────────────────┬────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  Supervisor │  ← 轻量路由 + 会话状态机
                    │  (协调层)   │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           │               │               │
      ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
      │ Analyst │   │  Quant  │   │  Coach  │  ...
      └────┬────┘   └────┬────┘   └────┬────┘
           │               │               │
           └───────────────┴───────────────┘
                           │
                    ┌──────▼──────┐
                    │  共享记忆层  │  ← L2 Shared: 所有 Agent 可读
                    │   (Redis)   │
                    └─────────────┘
```

**核心原则**：
1. Supervisor 负责路由和会话管理，不承担业务逻辑
2. SubAgent 之间不直接通信，通过共享记忆层实现上下文传递
3. SubAgent 可拒收非职责范围内的消息，交回 Supervisor 重路由
4. 会话状态由 Supervisor 统一管理，Agent 无需感知会话生命周期

---

## 3. 会话状态机

### 3.1 状态定义

```python
class SessionState(Enum):
    NONE = 'none'           # 空闲，所有消息走意图解析
    MULTI_TURN = 'multi_turn'  # 多轮对话中，特定 Agent 接管
    PAUSED = 'paused'          # 多轮对话被临时中断
```

### 3.2 状态流转图

```
用户消息
  │
  ├─ NONE
  │   └─ 意图解析 → 路由 → Agent
  │       ├─ Agent 接受 → 检查是否多轮
  │       │   └─ 是 → 变为 MULTI_TURN
  │       └─ Agent 拒收(need_reroute) → 重路由（最多2次）
  │
  ├─ MULTI_TURN
  │   └─ 转发给活跃 Agent
  │       ├─ Agent 接受 → 继续/结束
  │       └─ Agent 拒收(need_reroute)
  │           ├─ 暂停当前会话 + 设超时倒计时
  │           ├─ 路由到新 Agent（处理临时意图）
  │           └─ 超时后 → 总结记忆存入 L3 → 变为 NONE
  │
  └─ PAUSED
      ├─ 检查是否超时
      │   ├─ 已超时 → 总结 → 变为 NONE
      │   └─ 未超时
      │       ├─ Supervisor 判断当前消息属于原会话 → 恢复 MULTI_TURN
      │       └─ 当前消息属于其他 → 继续路由，保持 PAUSED
      └─ 新意图正常处理
```

### 3.3 会话数据结构（Redis）

```python
{
    'state': 'paused',
    'active_agent': 'coach',
    'paused_at': 1712150400,       # 暂停时间戳
    'pause_ttl': 300,               # 5 分钟超时
    'pause_context': '用户正在制定BTC交易计划...',  # 暂停时的上下文摘要
}
```

- `MULTI_TURN` 的 TTL 仍为 30 分钟（无操作超时）
- `PAUSED` 的 TTL 为 5 分钟（意图漂移超时）

---

## 4. SubAgent 拒收与重路由机制

### 4.1 问题场景

| 场景 | 描述 |
|------|------|
| 意图漂移 | 用户在多轮对话中突然切换话题 |
| 路由错误 | Supervisor 将消息路由给了错误的 Agent |
| 边界模糊 | 消息模糊，Supervisor 猜错了目标 |

### 4.2 领域边界定义

每个 SubAgent 的 system prompt 尾部追加领域边界描述：

```
### 领域边界
你的职责范围严格限定于：{domain_description}

不属于你职责的问题，必须只返回以下 JSON 格式，不加任何其他内容：
{"rejected": true, "reason": "简短原因", "suggested_agent": "agent名"}

属于你职责的，正常回答，不要包含 rejected 字段。
```

各 Agent 的领域定义：

| Agent | 职责范围 | 拒收时建议 |
|-------|---------|-----------|
| analyst | 市场行情分析（技术面/基本面）、K线解读、趋势判断、交易信号生成 | coach（交易计划）、risk_advisor（风控） |
| quant | 量化策略代码编写、策略优化、回测执行 | coach（计划） |
| coach | 交易计划制定、交易复盘、周报总结 | analyst（行情分析）、risk_advisor（风控） |
| risk_advisor | 仓位管理、止损建议、风险评估、风控规则制定 | analyst（行情分析）、coach（计划） |

### 4.3 代码改动

**AgentResult 新增字段**：

```python
# apps/agent/base.py
@dataclass
class AgentResult:
    task_id: str = ''
    success: bool = True
    data: Any = None
    error: str = ''
    token_used: int = 0
    duration_ms: int = 0
    need_reroute: bool = False      # 新增
    reroute_reason: str = ''        # 新增
    reroute_suggestion: str = ''    # 新增
```

**SubAgent 拒收检查**：

```python
# apps/agent/sub_agents.py
# _LLMAgent.handle() 中，LLM 回复后检查拒收信号
def _check_rejection(self, content: str) -> dict | None:
    try:
        match = re.search(r'\{[^}]*"rejected"\s*:\s*true[^}]*\}', content)
        if match:
            return json.loads(match.group(0))
    except (json.JSONDecodeError, AttributeError):
        pass
    return None
```

**Supervisor 重路由**：

```python
# apps/agent/supervisor.py
MAX_REROUTE = 2

async def _route_with_fallback(self, message, intent, attempted_agents=None):
    attempted = attempted_agents or set()
    agent_name = INTENT_TO_AGENT.get(intent)

    if not agent_name or agent_name in attempted or len(attempted) >= MAX_REROUTE:
        return await self._free_chat(message)

    attempted.add(agent_name)
    result = await self._route_to_agent(agent_name, message)

    if result.need_reroute:
        logger.info('Agent %s rejected: %s', agent_name, result.reroute_reason)

        # 优先用 SubAgent 建议的目标
        if result.reroute_suggestion and result.reroute_suggestion not in attempted:
            new_intent = self._agent_to_intent(result.reroute_suggestion)
            if new_intent:
                return await self._route_with_fallback(message, new_intent, attempted)

        # 重新解析意图（排除已尝试的 Agent）
        new_intent = await self._parse_intent(
            message.payload.get('text', ''),
            exclude_agents=list(attempted),
        )
        return await self._route_with_fallback(message, new_intent, attempted)

    return result
```

---

## 5. 多轮对话暂停与恢复

### 5.1 暂停触发条件

SubAgent 在多轮对话中返回 `need_reroute=True` 时触发。

### 5.2 Supervisor 判断是否恢复

当 PAUSED 状态下收到新消息时，Supervisor 调用 LLM 判断是否应恢复原会话：

```python
async def _judge_resume(self, message, paused_ctx) -> bool:
    """判断当前消息是否属于暂停中的对话的延续"""
    text = message.payload.get('text', '')
    pause_context = paused_ctx.get('pause_context', '')

    prompt = (
        f'用户有一个暂停中的对话（正在和 {paused_ctx["active_agent"]} 交互）：\n'
        f'暂停上下文：{pause_context}\n\n'
        f'用户当前消息：{text}\n\n'
        f'判断这条消息是否属于暂停中的对话的延续。只回答 true 或 false。'
    )
    resp = await self._llm.chat(
        system='你是一个意图判断助手，只回答 true 或 false。',
        user=prompt,
        max_tokens=10,
        temperature=0.0,
    )
    return resp.strip().lower().startswith('true')
```

### 5.3 会话超时归档

超时后总结暂停会话的记忆，存入 L3 长期记忆，状态变为 NONE：

```python
async def _archive_paused_session(self, user_id, ctx, session_mgr):
    agent_name = ctx.get('active_agent', '')
    pause_context = ctx.get('pause_context', '')

    # LLM 做一句话总结
    summary = await self._llm.chat(
        system='总结以下对话上下文为一句话。',
        user=pause_context,
        max_tokens=100,
        temperature=0.1,
    )

    # 存入 L3 长期记忆
    from apps.memory.manager import MemoryManager
    mm = MemoryManager(agent_type='supervisor', user_id=user_id)
    await mm.write_l3(
        content=f'[历史对话摘要-{agent_name}] {summary}',
        memory_type='conversation_summary',
        importance=3,
    )

    await session_mgr.clear_session_context(user_id)
```

---

## 6. 跨 Agent 共享记忆层

### 6.1 问题

当前记忆按 `agent_type` 隔离：
- `mem:l2:analyst:user123` — Analyst 私有
- `mem:l2:coach:user123` — Coach 私有

Analyst 的分析结论 Coach 看不到，导致关联任务上下文丢失。

### 6.2 解决方案

在 L2 层新增用户级共享通道：

```
L2 私有（不变）: mem:l2:{agent_type}:{user_id}  — Agent 私有上下文
L2 共享（新增）: mem:l2:shared:{user_id}        — 所有 Agent 可读，保留 200 条
```

### 6.3 代码改动

**MemoryManager.write_l2** — 写入时同时写共享区：

```python
async def write_l2(
    self,
    content: str,
    memory_type: str = 'general',
    importance: int = 1,
    shared: bool = True,  # 新增参数，默认写入共享区
) -> None:
    r = aioredis.from_url(settings.REDIS_URL)
    entry = {
        'content': content,
        'type': memory_type,
        'importance': importance,
        'ts': time.time(),
        'source_agent': self.agent_type,  # 标记来源
    }

    # 私有记忆（不变）
    agent_key = f'mem:l2:{self.agent_type}:{self.user_id}'
    await r.lpush(agent_key, json.dumps(entry))
    await r.ltrim(agent_key, 0, 99)
    await r.expire(agent_key, 86400)

    # 共享记忆（新增）
    if shared:
        shared_key = f'mem:l2:shared:{self.user_id}'
        await r.lpush(shared_key, json.dumps(entry))
        await r.ltrim(shared_key, 0, 199)  # 共享区保留更多
        await r.expire(shared_key, 86400)

    await r.aclose()
```

**MemoryManager.retrieve** — 查询时也查共享区：

```python
async def _retrieve_l2_shared(self, query: str, results: list, top_k: int) -> list:
    """查询用户级共享记忆"""
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        shared_key = f'mem:l2:shared:{self.user_id}'
        raw_list = await r.lrange(shared_key, 0, 49)
        await r.aclose()
        for raw in raw_list:
            entry = json.loads(raw)
            # 排除自己写的（已在私有区查过）
            if entry.get('source_agent') == self.agent_type:
                continue
            if query.lower() in entry.get('content', '').lower():
                results.append({'source': 'l2_shared', **entry})
            if len(results) >= top_k:
                return results
    except Exception as e:
        logger.debug(f'L2 shared retrieve skipped: {e}')
    return results
```

### 6.4 效果示例

```
10:00  用户: "分析一下 BTC 走势"
       → Analyst 执行，write_l2(shared=True)
       → 写入: "user: 分析一下BTC走势\nassistant: BTC当前处于上升通道..."

10:05  用户: "帮我做个 BTC 交易计划"
       → Coach 执行，retrieve("BTC 交易计划")
       → 返回 Analyst 的共享记忆（source: l2_shared）
       → Coach 的 prompt 自动注入历史分析结论
       → Coach 基于分析结论做交易计划

10:15  用户: "这个计划的风险大吗"
       → RiskAdvisor 执行，retrieve("BTC 计划 风险")
       → 返回 Coach 的计划摘要（source: l2_shared）
       → RiskAdvisor 基于交易计划评估风险
```

---

## 7. 多意图处理

### 7.1 策略：串行管道

用户一次输入包含多个意图时，按顺序串行执行：

```
用户: "分析ETH走势，评估风险"
         ↓
  _parse_intent() 返回:
  {
    "intents": [
      {"intent": "analyze_market", "params": {"symbol": "ETH"}},
      {"intent": "assess_risk", "params": {"symbol": "ETH"}}
    ]
  }
         ↓
  Supervisor 串行执行:
  1. route_to('analyst', ...) → 得到分析结果
  2. route_to('risk_advisor', ...) → 将分析结果作为上下文传入
         ↓
  合并结果返回用户
```

### 7.2 Supervisor 多意图处理

```python
async def _handle_multi_intent(self, message, intents: list) -> AgentResult:
    """串行处理多意图"""
    results = []
    for intent_item in intents:
        intent_name = intent_item['intent']
        params = intent_item.get('params', {})

        # 将前一个意图的结果注入 payload
        if results:
            prev_result = results[-1]
            payload = dict(message.payload)
            payload['previous_result'] = prev_result
            message.payload = payload

        # 应用当前意图的参数
        payload = dict(message.payload)
        payload.update(params)
        message.payload = payload

        agent_name = INTENT_TO_AGENT.get(intent_name)
        if agent_name:
            result = await self._route_with_fallback(message, intent_name)
            results.append(result)

    # 合并所有结果
    return self._merge_results(results)
```

---

## 8. 意图解析增强

### 8.1 三层降级机制

```
层级 1: LLM 解析（主路径）
  ↓ 失败条件: is_fallback(response)
层级 2: 重试一次（temperature 降低）
  ↓ 失败条件: JSON 解析失败
层级 3: 规则引擎兜底
  ↓ 失败条件: 无匹配规则
最终: free_chat
```

### 8.2 规则引擎降级

```python
FALLBACK_RULES = [
    (r'(分析|行情|走势|K线|趋势).*(BTC|ETH|币|市场)', 'analyze_market'),
    (r'(回测|测试策略|历史数据)', 'run_backtest'),
    (r'(风险|止损|仓位|风控)', 'assess_risk'),
    (r'(计划|复盘|总结|周报)', 'create_plan'),
    (r'(策略|代码|编写)', 'generate_strategy'),
]
```

---

## 9. 文件改动清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `apps/agent/base.py` | 修改 | `AgentResult` 新增 `need_reroute`, `reroute_reason`, `reroute_suggestion` |
| `apps/agent/sub_agents.py` | 修改 | `_LLMAgent.handle()` 加拒收检查；prompt 加领域边界；新增 `_check_rejection()` |
| `apps/agent/supervisor.py` | 重写 | 重写 `handle()` 加会话状态机；新增 `_route_with_fallback()`, `_judge_resume()`, `_archive_paused_session()`, `_handle_multi_intent()`, `_merge_results()` |
| `apps/agent/session_manager.py` | 修改 | `SessionState` 新增 `NONE`, `PAUSED`；会话数据加 `paused_at`, `pause_ttl`, `pause_context` |
| `apps/memory/manager.py` | 修改 | `write_l2()` 加 `shared` 参数；同时写共享区；`retrieve()` 加 `_retrieve_l2_shared()` |
| `prompts/v1/analyst.txt` | 修改 | 尾部追加领域边界描述 |
| `prompts/v1/coach.txt` | 修改 | 尾部追加领域边界描述 |
| `prompts/v1/quant.txt` | 修改 | 尾部追加领域边界描述 |
| `prompts/v1/risk_advisor.txt` | 修改 | 尾部追加领域边界描述 |
| `prompts/v1/supervisor.txt` | 修改 | 更新多意图处理指导 |

### 9.1 改动量估算

| 文件 | 新增行数 | 修改行数 |
|------|---------|---------|
| `base.py` | 3 | 0 |
| `sub_agents.py` | ~30 | ~5 |
| `supervisor.py` | ~150 | ~30（重构）|
| `session_manager.py` | 0 | ~20 |
| `memory/manager.py` | ~40 | ~10 |
| Prompt 文件（4个） | ~8 | 0 |
| **合计** | ~231 | ~65 |

---

## 10. 实现优先级

### 第一优先级（核心回路）
1. `AgentResult` 新增字段
2. `SessionState` 新增状态
3. Supervisor 会话状态机（handle 重写）
4. SubAgent 拒收检查
5. Supervisor 重路由

### 第二优先级（记忆共享）
6. MemoryManager 共享通道
7. SubAgent `write_l2()` 调用加 `shared=True`

### 第三优先级（增强功能）
8. 多意图串行处理
9. 意图解析三层降级
10. 会话超时归档

---

## 11. 测试场景

| # | 场景 | 预期行为 |
|---|------|---------|
| T1 | 用户在多轮对话中问了一个临时问题 | 会话暂停 → 处理临时问题 → 用户确认恢复 → 继续原会话 |
| T2 | 用户在多轮对话中切换话题，5分钟未回来 | 会话超时 → 总结存入 L3 → 用户新消息正常路由 |
| T3 | Supervisor 将消息路由给错误的 Agent | Agent 拒收 → Supervisor 重路由 → 正确 Agent 处理 |
| T4 | 用户: "分析BTC并做交易计划" | 串行执行：先分析 → 再做计划（计划中引用分析结论） |
| T5 | Coach 做计划时引用了之前 Analyst 的分析 | 通过共享记忆层获取 |
| T6 | LLM 完全不可用 | 规则引擎兜底 → 仍无法匹配 → free_chat 友好提示 |
