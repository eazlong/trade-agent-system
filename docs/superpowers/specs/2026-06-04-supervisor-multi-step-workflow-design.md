# Supervisor 多步骤工作流设计

## 背景

当前 Supervisor 每次路由只能派发到单个 Agent。当用户任务需要多 Agent 协作时（如"全自动策略研究"需要 researcher 调研 + quant 实现/回测），Supervisor 只能被动响应拒收后的重路由，无法主动拆分并链式执行。

## 目标

Supervisor 能自动将复杂意图拆分为多步骤工作流，按顺序依次派发各步骤到对应 Agent，自动注入上一步结果到下一步上下文，最终汇总返回。

## 架构

### 数据流

```
用户消息 → Supervisor._normal_route
  ↓
_parse_intent → LLM 判断是否需要拆分
  ├─ 单步骤 → 现有 _route_to_agent（不变）
  └─ 多步骤 → 生成 workflow_plan → _execute_workflow()
       ↓
    [Step 1: researcher] → [Step 2: quant] → ...
       ↓
    依次执行，每步结果自动注入下一步 payload
       ↓
    汇总所有结果 → AgentResult
```

### workflow_plan 结构

LLM 解析时返回的 JSON 格式：

```json
{
  "workflow_plan": {
    "summary": "全自动策略研究：先由 researcher 调研高胜率策略，再由 quant 实现并回测",
    "steps": [
      {
        "agent": "researcher",
        "message": "请在网络上研究一个高胜率交易策略思路，分析其核心逻辑和适用场景。"
      },
      {
        "agent": "quant",
        "message": "基于上述研究结果，实现该策略的逻辑代码，执行回测并优化参数。"
      }
    ]
  }
}
```

### 核心方法

#### `_execute_workflow(steps, message, on_tool_result)`

- 依次遍历 `steps`，每个步骤调用 `_route_to_agent`
- 上一步的 `result.data` 自动注入到下一步的 `payload` 中（`previous_agent_response`、`previous_agent_name`）
- 任何一步失败立即中止，返回错误
- 全部完成后汇总所有步骤结果

### _normal_route 接入点

在 `_parse_intent` 返回后：
1. 检查返回内容是否包含 `_workflow_plan`
2. 如果有，提取 `steps` 数组，调用 `_execute_workflow()`
3. 如果没有，走现有 `_route_to_agent()` 逻辑（不变）

## 兼容性

- 单步骤任务完全走现有路径，零影响
- `AgentResult` 无需修改
- 不触碰 Agent 注册表和 task 系统
- `execute_recurring_agent_task` 中 `agent_name='supervisor'` 已支持，无需额外改动

## 错误处理

- 某步骤失败：立即返回，错误信息包含步骤编号和 agent 名称
- 某步骤超时：通过现有 `need_reroute` 机制处理
- 全部成功：返回汇总结果，包含每个步骤的 agent 和输出

## 测试

- 单元测试：模拟 `_parse_intent` 返回 `_workflow_plan`，验证 `_execute_workflow` 依次调用 `_route_to_agent` 且正确注入上下文
- 集成测试：`hourly_strategy_research` 定时任务，验证 supervisor→researcher→quant 完整链路
