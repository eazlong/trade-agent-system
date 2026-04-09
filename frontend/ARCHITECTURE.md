# TradeClaw 前端架构方案

> 本文档定义量化交易仪表盘 React+TypeScript 前端项目的完整架构。参考设计：`frontend/tradeclaw.html`

---

## 1. 项目概述

**项目名称**：TradeClaw Dashboard
**项目类型**：数据密集型实时交易监控仪表盘
**核心功能**：智能体集群状态监控、策略净值曲线、实时信号流、持仓管理、风险仪表盘
**目标用户**：量化交易员、交易系统运维人员

---

## 2. 技术栈

| 层级 | 技术选型 | 版本 | 用途 |
|------|----------|------|------|
| 构建工具 | Vite | 5.x | 快速开发服务器、HMR、生产构建 |
| UI 框架 | React | 18.x | 组件化 UI |
| 类型系统 | TypeScript | 5.x | 静态类型检查 |
| 样式方案 | Tailwind CSS | 3.x | 原子化 CSS + 自定义主题 |
| 状态管理 | Zustand | 4.x | 轻量级全局状态 |
| 图表库 | Recharts | 2.x | 净值曲线、风险仪表盘 |
| 路由 | React Router | 6.x | 多视图路由（总览/智能体/信号/回测/配置） |
| 模拟数据 | Mock Service Worker | 1.x | API 模拟 + 真实数据注入 |
| 单元测试 | Vitest + Testing Library | - | 组件和 Hook 测试 |
| E2E 测试 | Playwright | - | 关键用户流程 |

---

## 3. 项目目录结构

```
frontend/
├── public/
│   └── favicon.svg
├── src/
│   │
│   ├── main.tsx                     # 应用入口
│   ├── App.tsx                      # 根组件 + 路由配置
│   │
│   ├── components/                  # 共享 UI 组件
│   │   ├── ui/                      # 底层 UI 原子组件
│   │   │   ├── Button.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Toggle.tsx
│   │   │   ├── Select.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Modal.tsx
│   │   │   └── ScrollArea.tsx
│   │   │
│   │   ├── charts/                  # 图表组件
│   │   │   ├── PnLChart.tsx         # 净值曲线（Recharts AreaChart）
│   │   │   ├── Sparkline.tsx        # 迷你折线图
│   │   │   └── RiskGauge.tsx        # 风险仪表盘（Recharts RadialBar）
│   │   │
│   │   └── layout/                  # 布局组件
│   │       ├── Topbar.tsx            # 顶栏（Logo + 导航 + 市场行情）
│   │       ├── Sidebar.tsx          # 侧边栏（Agent 集群 + 账户概况）
│   │       └── RightPanel.tsx       # 右侧面板（持仓 + 风险 + 执行）
│   │
│   ├── features/                    # 功能模块（按领域划分）
│   │   ├── dashboard/               # 总览模块
│   │   │   ├── DashboardPage.tsx   # 总览页容器
│   │   │   ├── MetricsGrid.tsx      # 指标卡片网格（净值/夏普/回撤）
│   │   │   ├── PnLCard.tsx          # 净值曲线卡片
│   │   │   ├── AgentStatusCard.tsx  # 智能体状态网格
│   │   │   ├── SignalsCard.tsx      # 实时信号列表
│   │   │   └── LogCard.tsx          # Agent 通信日志
│   │   │
│   │   ├── agents/                  # 智能体模块
│   │   │   ├── AgentsPage.tsx       # 智能体管理页
│   │   │   ├── AgentCard.tsx         # 单个 Agent 详情卡片
│   │   │   └── AgentDetail.tsx      # Agent 详情弹窗
│   │   │
│   │   ├── signals/                 # 信号模块
│   │   │   ├── SignalsPage.tsx      # 信号管理页
│   │   │   ├── SignalItem.tsx        # 单条信号
│   │   │   └── SignalFilter.tsx      # 信号筛选器
│   │   │
│   │   ├── backtest/                # 回测模块
│   │   │   ├── BacktestPage.tsx      # 回测页
│   │   │   ├── BacktestForm.tsx      # 回测配置表单
│   │   │   └── BacktestResults.tsx   # 回测结果展示
│   │   │
│   │   ├── strategies/              # 策略模块
│   │   │   ├── StrategyList.tsx      # 策略列表（侧栏复用）
│   │   │   └── StrategyModal.tsx     # 创建策略弹窗
│   │   │
│   │   └── settings/                # 配置模块
│   │       ├── SettingsPage.tsx      # 配置页
│   │       └── SettingsForm.tsx       # 配置表单
│   │
│   ├── stores/                      # Zustand 状态管理
│   │   ├── marketStore.ts           # 市场行情状态（价格/涨跌/实时 ticker）
│   │   ├── agentStore.ts            # Agent 集群状态
│   │   ├── signalStore.ts           # 信号队列状态
│   │   ├── portfolioStore.ts         # 账户/持仓状态
│   │   ├── strategyStore.ts         # 策略列表 + 启停状态
│   │   ├── riskStore.ts             # 风险指标状态
│   │   └── uiStore.ts               # UI 状态（Tab/Modal/选中项）
│   │
│   ├── hooks/                       # 自定义 Hooks
│   │   ├── useSimulatedData.ts      # 模拟数据生成 Hook
│   │   ├── useInterval.ts           # 定时刷新 Hook
│   │   └── useMediaQuery.ts         # 响应式 Hook
│   │
│   ├── lib/                         # 工具库
│   │   ├── formatters.ts            # 数字/时间/货币格式化
│   │   └── cn.ts                    # className 合并工具（clsx + tailwind-merge）
│   │
│   ├── mocks/                       # 模拟数据层
│   │   ├── handlers.ts              # MSW 请求处理
│   │   ├── mockData.ts              # 静态模拟数据
│   │   └── generators.ts            # 动态数据生成器（价格波动/信号/日志）
│   │
│   ├── types/                       # TypeScript 类型定义
│   │   ├── market.ts               # 市场数据类型
│   │   ├── agent.ts                # Agent 类型
│   │   ├── signal.ts               # 信号类型
│   │   ├── portfolio.ts           # 持仓/账户类型
│   │   ├── strategy.ts             # 策略类型
│   │   └── risk.ts                 # 风险指标类型
│   │
│   └── styles/
│       └── globals.css              # 全局样式 + CSS 变量
│
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts               # Tailwind 自定义主题
├── postcss.config.js
├── vitest.config.ts
├── playwright.config.ts
└── ARCHITECTURE.md
```

---

## 4. 组件清单与职责

### 4.1 布局组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `Topbar` | `components/layout/Topbar.tsx` | Logo、导航 Tab（总览/智能体/信号/回测/配置）、市场行情芯片（BTC/ETH/SOL 实时价格）、状态指示灯、新建策略按钮 |
| `Sidebar` | `components/layout/Sidebar.tsx` | Agent 集群列表（带状态指示灯）、账户概况统计、活跃策略列表 |
| `RightPanel` | `components/layout/RightPanel.tsx` | 当前持仓列表、风险仪表盘（SVG 量规）、近期执行记录、模型调用统计 |

### 4.2 功能模块组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `MetricsGrid` | `features/dashboard/MetricsGrid.tsx` | 三列网格展示：净值、夏普比率、最大回撤 |
| `PnLCard` | `features/dashboard/PnLCard.tsx` | 净值曲线卡片，内含 Recharts AreaChart，支持时间范围切换 |
| `AgentStatusCard` | `features/dashboard/AgentStatusCard.tsx` | 2x3 网格展示 6 个 Agent 卡片，含进度条和置信度 |
| `SignalsCard` | `features/dashboard/SignalsCard.tsx` | 实时信号列表（BUY/SELL/WATCH 三种类型），滚动加载 |
| `LogCard` | `features/dashboard/LogCard.tsx` | Agent 间通信日志流，实时追加新条目 |
| `DashboardPage` | `features/dashboard/DashboardPage.tsx` | 总览页容器，组合所有子组件 |
| `StrategyModal` | `features/strategies/StrategyModal.tsx` | 创建策略弹窗表单（名称、品种、类型、周期、Agent 选择） |
| `StrategyList` | `features/strategies/StrategyList.tsx` | 策略列表，含开关启停 |

### 4.3 UI 原子组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `Badge` | `components/ui/Badge.tsx` | 状态标签（主控/运行/告警/待机） |
| `Toggle` | `components/ui/Toggle.tsx` | 策略启停开关 |
| `Modal` | `components/ui/Modal.tsx` | 模态弹窗（聚焦+背景模糊） |
| `RiskGauge` | `components/charts/RiskGauge.tsx` | 风险仪表盘，SVG 弧形量规 |

---

## 5. Tailwind 自定义主题配置

基于 `tradeclaw.html` 的 CSS 变量定义，映射为 Tailwind 主题扩展：

```typescript
// tailwind.config.ts
import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // 背景层级
        bg: {
          base:   '#0a0c0f',  // 页面背景
          1:      '#0e1117',  // 卡片/面板背景
          2:      '#131720',  // 指标框/输入框背景
          3:      '#1a2030',  // 选中态背景
        },
        // 边框
        border: {
          DEFAULT: 'rgba(255,255,255,0.07)',
          subtle:  'rgba(255,255,255,0.12)',
        },
        // 文本
        text: {
          primary:   '#e8ecf2',
          secondary: '#8b95a8',
          muted:     '#4d5766',
        },
        // 语义色
        gain: {
          DEFAULT: '#00e676',
          dim:    'rgba(0,230,118,0.12)',
          glow:   'rgba(0,230,118,0.25)',
        },
        loss: {
          DEFAULT: '#ff5252',
          dim:    'rgba(255,82,82,0.12)',
        },
        warn: {
          DEFAULT: '#ffab00',
          dim:    'rgba(255,171,0,0.12)',
        },
        blue: {
          DEFAULT: '#40c4ff',
          dim:    'rgba(64,196,255,0.1)',
        },
        purple: {
          DEFAULT: '#b388ff',
          dim:    'rgba(179,136,255,0.1)',
        },
        teal: {
          DEFAULT: '#64ffda',
          dim:    'rgba(100,255,218,0.1)',
        },
      },
      fontFamily: {
        ui:   ['Outfit', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
      },
      fontSize: {
        '2xs': ['9px',  { lineHeight: '12px', letterSpacing: '0.1em' }],
      },
      borderRadius: {
        card: '10px',
        item: '8px',
      },
      boxShadow: {
        'glow-green': '0 0 6px rgba(0,230,118,0.25)',
        'glow-amber': '0 0 6px rgba(255,171,0,0.25)',
      },
      animation: {
        pulse: 'pulse 2s cubic-bezier(0.4,0,0.6,1) infinite',
        blink: 'blink 1.4s ease-in-out infinite',
        'scan-line': 'scanline 8s linear infinite',
      },
      keyframes: {
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
        blink: {
          '0%, 100%': { opacity: '0.2' },
          '50%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
} satisfies Config
```

**关键设计决策**：
- 所有颜色通过 Tailwind 语义化 token（`bg-gain`、`text-loss`、`border-warn` 等）访问
- 不使用任意值（arbitrary values），确保主题一致性
- `fontFamily.ui` 和 `fontFamily.mono` 覆盖默认字体堆栈

---

## 6. 状态管理方案

使用 **Zustand** 分域管理，每条 Store 职责单一：

### 6.1 Store 划分

| Store | 文件 | 状态内容 | 更新频率 |
|-------|------|----------|----------|
| `marketStore` | `stores/marketStore.ts` | BTC/ETH/SOL 价格、涨跌、基准价格 | ~1.8s（模拟 ticker） |
| `agentStore` | `stores/agentStore.ts` | Agent 列表、状态、置信度、进度 | ~2.5s |
| `signalStore` | `stores/signalStore.ts` | 信号队列（类型/符号/描述/置信度/时间） | 事件驱动 |
| `portfolioStore` | `stores/portfolioStore.ts` | 总资产、今日盈亏、持仓列表 | 事件驱动 |
| `strategyStore` | `stores/strategyStore.ts` | 策略列表、启停状态 | 用户操作 |
| `riskStore` | `stores/riskStore.ts` | 风险等级、VaR、贝塔、集中度、胜率 | ~5s |
| `uiStore` | `stores/uiStore.ts` | 当前 Tab、选中 Agent、Modal 开关 | 用户操作 |

### 6.2 状态更新模式

```typescript
// marketStore 示例
interface MarketStore {
  tickers: Record<string, TickerData>
  updatePrice: (symbol: string, price: number) => void
}

export const useMarketStore = create<MarketStore>((set) => ({
  tickers: initialTickers,
  updatePrice: (symbol, price) =>
    set((state) => ({
      tickers: {
        ...state.tickers,
        [symbol]: {
          ...state.tickers[symbol],
          price,
          changePct: ((price - state.tickers[symbol].base) / state.tickers[symbol].base) * 100,
        },
      },
    })),
}))
```

### 6.3 模拟数据刷新

使用 `useSimulatedData` Hook 集中管理所有定时模拟：

```typescript
// hooks/useSimulatedData.ts
export function useSimulatedData() {
  const updatePrice = useMarketStore((s) => s.updatePrice)
  const updateAgents = useAgentStore((s) => s.updateMetrics)
  const addLog = useAgentStore((s) => s.addLog)

  useEffect(() => {
    const tickerInterval = setInterval(() => { /* 小幅随机波动 */ }, 1800)
    const agentInterval = setInterval(() => { /* 置信度浮动 */ }, 2500)
    const logInterval = setInterval(() => { /* 生成新日志 */ }, 3200)
    return () => {
      clearInterval(tickerInterval)
      clearInterval(agentInterval)
      clearInterval(logInterval)
    }
  }, [updatePrice, updateAgents, addLog])
}
```

---

## 7. 数据流设计

### 7.1 整体数据流

```
Mock Data (generators.ts)
        │
        ▼
  Zustand Stores (domain slices)
        │
        ├── marketStore  ──►  Topbar / Sidebar.stats
        ├── agentStore   ──►  Sidebar.agents / AgentStatusCard / LogCard
        ├── signalStore  ──►  SignalsCard
        ├── portfolioStore ─►  RightPanel.positions
        ├── riskStore    ──►  RightPanel.riskGauge
        └── strategyStore ─►  Sidebar.strategies
        │
        ▼
  React Components (declarative UI)
        │
        ▼
  Recharts (PnLChart / RiskGauge)
```

### 7.2 模拟数据生成器

参考 `tradeclaw.html` 的模拟逻辑：

| 生成器 | 文件 | 逻辑 |
|--------|------|------|
| 价格 ticker | `mocks/generators.ts` | `price += (Math.random() - 0.5) * step`，限制波动范围 |
| Agent 置信度 | `mocks/generators.ts` | 在 `[min, max]` 范围内随机游走，逐步逼近 |
| 信号队列 | `mocks/generators.ts` | 从预定义模板池轮询，附带随机化置信度 |
| 通信日志 | `mocks/generators.ts` | 从模板库选择，预设 Agent 颜色和时间戳 |
| PnL 曲线 | `mocks/generators.ts` | 30 天数据，每日 `v += (Math.random() - 0.45) * 0.015` |

### 7.3 组件数据消费

各组件只订阅自己需要的状态片段，避免不必要的重渲染：

```typescript
// DashboardPage.tsx - 组件级订阅
const DashboardPage = () => {
  const nav = usePortfolioStore((s) => s.nav)
  const sharpe = useRiskStore((s) => s.sharpe)
  const mdd = useRiskStore((s) => s.maxDrawdown)
  const signals = useSignalStore((s) => s.signals)
  const agents = useAgentStore((s) => s.agents)
  const logs = useAgentStore((s) => s.logs)

  return (
    <div className="grid gap-3">
      <MetricsGrid nav={nav} sharpe={sharpe} mdd={mdd} />
      <div className="grid grid-cols-2 gap-3">
        <PnLCard />
        <AgentStatusCard agents={agents} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <SignalsCard signals={signals} />
        <LogCard logs={logs} />
      </div>
    </div>
  )
}
```

---

## 8. 技术决策记录（ADR）

### ADR-001: 样式方案选型
**决策**：Tailwind CSS + 自定义主题配置
**理由**：
- 参考设计使用纯 CSS 变量，需要迁移为 Tailwind token 实现主题一致性
- 原子化 CSS 契合数据密集型仪表盘的快速迭代需求
- 与现有 `tailwind.config.ts` 模式兼容
**替代方案**：CSS Modules + CSS 变量（维护成本更高）

### ADR-002: 图表库选型
**决策**：Recharts
**理由**：
- React 原生，支持 TypeScript
- 与 Zustand 状态联动自然（直接传 data prop）
- 轻量（无 ECharts 体积开销），覆盖面积图和径向图需求
**替代方案**：Chart.js + react-chartjs-bridge（已用于参考设计，但 React 集成较弱）

### ADR-003: 状态粒度
**决策**：按领域分域，每域独立 Store
**理由**：交易仪表盘各数据维度更新频率不同（ticker 1.8s vs 策略启停用户操作），分离 Store 避免联动重渲染
**替代方案**：单一 Store + 多 Slice（增加耦合）

### ADR-004: 模拟数据层
**决策**：MSW (Mock Service Worker) + 本地生成器
**理由**：
- 开发阶段无需后端接口
- MSW 可拦截真实 fetch，便于后续切换真实 API
- 生成器函数易于扩展和调整
**替代方案**：直接 Hardcode 数据（不灵活）

### ADR-005: 项目结构
**决策**：`features/` 目录按领域组织，而非 `components/` 扁平划分
**理由**：
- 量化交易仪表盘功能边界清晰（Dashboard/Agents/Signals/Backtest/Settings）
- 每个 feature 可内聚自己的组件、store、types
- 便于多人并行开发不同模块

### ADR-006: 路由方案
**决策**：React Router v6
**理由**：参考设计有 5 个 Tab（总览/智能体/信号/回测/配置），需要 URL 状态支持书签和分享

### ADR-007: 字体方案
**决策**：Google Fonts (Outfit + IBM Plex Mono) + Vite 预加载
**理由**：参考设计明确指定，Outfit 适合 UI 文本，IBM Plex Mono 适合数字展示

---

## 9. 布局映射（参考设计 → React 组件）

```
┌──────────────────────────────────────────────────────────────┐
│ Topbar (52px)                                               │
│ [Logo] [总览|智能体|信号|回测|配置]         [BTC] [ETH] [SOL] │
├──────────┬───────────────────────────────────┬───────────────┤
│ Sidebar  │ Main Content                      │ RightPanel   │
│ (220px)  │                                   │ (280px)      │
│          │ MetricsGrid (grid-3)              │ Positions    │
│ Agents   │   净值 | 夏普比率 | 最大回撤        │ RiskGauge    │
│ -------- │                                   │ Recent Trades│
│ Account  │ grid-2                             │ API Stats    │
│ -------- │   PnLCard | AgentStatusCard       │              │
│ Strategies│                                   │              │
│          │ grid-2                             │              │
│          │   SignalsCard | LogCard           │              │
└──────────┴───────────────────────────────────┴─────────────┘
```

---

## 10. 快速启动

```bash
cd frontend

# 安装依赖
npm install

# 开发服务器
npm run dev

# 类型检查
npm run type-check

# 测试
npm run test

# 构建
npm run build

# E2E 测试
npm run test:e2e
```

---

*文档版本：1.0 | 生成日期：2026-04-09*
