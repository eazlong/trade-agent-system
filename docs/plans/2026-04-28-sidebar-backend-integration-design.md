# Sidebar 智能体集群后端对接设计

**日期**: 2026-04-28
**范围**: Sidebar.tsx 智能体集群模块 + DashboardContext

## 概述

将 Sidebar.tsx 中硬编码的智能体列表、账户概况和策略数据替换为后端 API 实时数据，并为智能体条目增加页面跳转功能。

## 架构

### DashboardContext

新建 `src/context/DashboardContext.tsx`，在 dashboard layout 层提供全局状态：

```
DashboardProvider (mounted in (dashboard)/layout.tsx)
├── agents: AgentInfo[]     — agentApi.listAgents()
├── summary: TradingSummary | null — tradingApi.getSummary()
├── loading: boolean
├── refresh: () => void
└── 30s 轮询
```

使用原因：Overview、Agents、Sidebar 三处需要同一份数据，避免重复请求。

### Sidebar 智能体集群模块

1. **数据源**：通过 `useDashboard()` 消费 Context 中的 `agents`
2. **跳转映射**（`AGENT_ROUTE_MAP`）：

| Agent | 路由 |
|---|---|
| supervisor | /agents |
| analyst | /agents |
| quant | /backtest |
| risk_advisor | /overview |
| trading_frame | /trading |
| assist_frame | /logs |
| coach | /agents |

3. **状态映射**：`ready→就绪` `standby→待命` `running→运行中` `stopped→已停止`
4. **交互**：点击高亮 + `router.push()` 跳转
5. **加载态**：骨架占位 7 行
6. **错误态**：错误文本 + 重试按钮

### 文件变更

| 文件 | 操作 |
|---|---|
| `src/context/DashboardContext.tsx` | 新建 |
| `src/app/(dashboard)/layout.tsx` | 挂载 Provider |
| `src/components/layout/Sidebar.tsx` | 重写智能体集群 + 账户概况模块 |
| `src/lib/api.ts` | 新增 Agent 状态映射辅助函数 |
