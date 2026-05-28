# 回测列表前端重构设计

**日期**: 2026-05-28
**状态**: 已批准（含架构评审修订）

## 问题陈述

前端展示回测记录时存在两个核心痛点：
1. 无法区分哪些记录属于同一个网格搜索任务
2. 无法在列表页看到回测的策略参数和进阶指标，必须点进详情页

## 需求汇总

通过 brainstorming 与用户逐节确认：

| 决策点 | 选择 |
|--------|------|
| 分组方式 | 树形表格内折叠（▶/▼ 箭头） |
| 主行内容 | 保留现有 6 基础指标 + 新增参数列（前 3-4 个 key=value，hover 展示全部） |
| 子行布局 | 左右分栏：左侧策略参数 Tag，右侧完整指标（收益率/夏普/回撤/Sortino/Calmar/Profit Factor） |
| 筛选功能 | 最小化：搜索框（策略名/品种模糊搜索）+ 类型筛选（全部/网格搜索/单次回测） |
| 实现方案 | 纯前端改造（方案 A）— 后端最小改动 |

## 后端改动

### 1. Serializer 暴露 metrics 字段

- `BacktestResultSerializer` 和 `BacktestDetailSerializer` 的 `fields` 中加入 `"metrics"`（模型中已有该 JSONField，只需 serializer 暴露）
- 前端 `BacktestResult` TypeScript 接口新增 `metrics?: Record<string, any>`

### 2. 列表 API 聚合（`?grouped=1`）

- 复用 `GET /api/backtest/results/`，新增可选查询参数 `grouped=1`
- 不传时保持原有扁平列表行为（向后兼容）
- 传入时返回结构（按 groups 分页，不是按 records）：

```json
{
  "groups": [
    {
      "type": "grid_search",
      "job_id": "uuid",
      "job_name": "MACD突破网格",
      "symbol": "BTC/USDT",
      "timeframe": "1h",
      "status": "completed",
      "total_combinations": 16,
      "completed": 16,
      "best_return_pct": 12.5,
      "best_sharpe": 1.82,
      "created_at": "2026-05-28T11:30:00Z",
      "results": [...]
    },
    {
      "type": "single",
      "result": {...}
    }
  ],
  "group_count": 42,
  "total_records": 158,
  "num_pages": 5,
  "current_page": 1
}
```

**分页规则**：按 groups 分页，一个 group（无论含多少个子结果）是单个分页单元。

**查询优化**：使用 `prefetch_related` 或 `Prefetch` 避免 N+1 查询。

**响应校验**：建议新增 `BacktestGroupedResponseSerializer` 校验输出形状。

### 3. GridSearchJob 展示名

- 模型无 `name` 字段，**不新增迁移**。使用计算字段生成展示名：
  - 优先使用 `strategy.name`（关联策略名）
  - 组合显示：`f"{strategy_name} - {symbol}/{timeframe}"`
- 后端 serializer 中通过 `SerializerMethodField` 计算 `job_name`

## 前端架构

### 文件改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/tradeclaw-web/src/lib/api.ts` | 修改 | 新增 `getGroupedList()` + TypeScript 类型定义 |
| `frontend/tradeclaw-web/src/components/backtest/BacktestFilterBar.tsx` | 新建 | 搜索框 + 类型筛选下拉 |
| `frontend/tradeclaw-web/src/components/backtest/BacktestTreeTable.tsx` | 新建 | 树形表格组件（纯 UI，只管展开/折叠） |
| `frontend/tradeclaw-web/src/app/(dashboard)/backtest/page.tsx` | 修改 | 数据获取 + 过滤状态 + 组合子组件 |
| `frontend/tradeclaw-web/src/app/(dashboard)/backtest/[id]/page.tsx` | 修改 | 详情页接入 metrics 字段 |

### 组件职责分离

**BacktestListPage**（page.tsx）：
- 管理 `loading`、`groups`、`error` 状态
- 调用 `getGroupedList()` 获取数据
- 组合 `<BacktestFilterBar>` + `<BacktestTreeTable>`
- 传递已过滤的数据给子组件

**BacktestFilterBar**：
- 管理 `searchQuery`、`filterType` 本地状态
- 通过回调 `onSearchChange`、`onFilterChange` 通知父组件

**BacktestTreeTable**：
- 仅管理 `expandedGroups: Set<string>` 展开状态
- 接收 `groups: BacktestGroup[]`（已过滤数据）作为 props
- 纯 UI 组件，不含筛选/搜索逻辑

### TypeScript 类型定义

```typescript
interface BacktestGroup {
  type: 'grid_search' | 'single' | 'orphaned_grid_search';
  job_id?: string;
  job_name?: string;
  symbol: string;
  timeframe: string;
  status?: 'running' | 'completed' | 'failed';
  total_combinations?: number;
  completed?: number;
  best_return_pct?: number;
  best_sharpe?: number;
  created_at: string;
  results?: BacktestResult[];  // for grid_search
  result?: BacktestResult;     // for single
}

interface GroupedBacktestResponse {
  groups: BacktestGroup[];
  group_count: number;
  total_records: number;
  num_pages: number;
  current_page: number;
}
```

### BacktestTreeTable 渲染逻辑

- 网格搜索行（折叠）：`▶` 箭头 + 类型标签 + 策略名 + 品种/周期 + 最佳指标摘要 + 参数摘要 + 操作 + 状态指示器（running 显示 spinner, failed 显示错误图标）
- 网格搜索行（展开）：`▼` 箭头 + 下方紧跟子行列表
- 子行：左边距缩进 30px，左右分栏 Grid（参数 Tag 列表 + 6 格指标）
- 单次回测行：与现有行渲染逻辑一致

### 过滤逻辑

- 搜索：匹配策略名或品种（前端 filter）
  - 子项匹配时：自动展开 group，高亮匹配行，淡化非匹配兄弟行
  - 单次回测匹配时：正常显示
- 类型筛选：all 显示全部 / grid 只显示网格搜索分组 / single 只显示单次回测

## 数据流

```
列表页 mount
  → GET /api/backtest/results/?grouped=1
  → 后端聚合 BacktestResult + GridSearchJob（prefetch_related 避免 N+1）
  → 返回 groups 数组（按 groups 分页）
  → 前端渲染树形表格
  → 用户输入搜索/筛选 → 前端过滤 groups 数据
  → 用户点击 ▶ 展开 → 纯前端状态切换（无需额外 API 调用）
```

展开不需要二次请求，因为 grouped API 一次性返回全部数据。当前数据量可控（一次网格搜索最多几十条），后续数据量爆炸时再改为懒加载。

## 加载状态

- 初始加载：整表 skeleton（复用现有 "加载中..." 文本或添加骨架行）
- 分页切换：保持当前内容，顶部加载指示器

## 错误边界

| 场景 | 处理 |
|------|------|
| GridSearchJob 已删除但子记录仍在 | 降级为 `orphaned_grid_search`，灰色提示 |
| metrics 字段为 null | 子行指标区显示 "—" 占位 |
| 参数为空对象 | 参数列显示 "无参数" |
| API 请求失败 | 显示用户可见错误提示（不再静默吞错） |
| 运行中的网格搜索 | 子行显示 skeleton + spinner |

## 测试

- 列表页加载 grouped API 数据渲染（快照测试）
- 展开/折叠交互（单元测试）
- 搜索/筛选功能过滤正确 + 子项匹配自动展开（单元测试）
- 子行参数和指标展示完整（快照测试）
- API 错误状态展示（单元测试）
