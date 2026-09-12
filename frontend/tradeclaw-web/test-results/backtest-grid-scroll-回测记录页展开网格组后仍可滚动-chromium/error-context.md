# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: backtest-grid-scroll.spec.ts >> 回测记录页展开网格组后仍可滚动
- Location: e2e/backtest-grid-scroll.spec.ts:93:5

# Error details

```
Error: 展开前内容应超出视口

expect(received).toBeGreaterThan(expected)

Expected: > 100
Received:   0
```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - button "Open Next.js Dev Tools" [ref=e7] [cursor=pointer]:
    - img [ref=e8]
  - alert [ref=e11]
  - generic [ref=e12]:
    - generic [ref=e14]:
      - generic [ref=e15]:
        - img [ref=e17]
        - generic [ref=e20]:
          - generic [ref=e21]: TradeClaw
          - generic [ref=e22]: Agent OS · v2.4.1
      - generic [ref=e23]:
        - link "总览" [ref=e24] [cursor=pointer]:
          - /url: /overview
        - link "智能体" [ref=e25] [cursor=pointer]:
          - /url: /agents
        - link "回测" [ref=e26] [cursor=pointer]:
          - /url: /backtest
        - link "交易" [ref=e27] [cursor=pointer]:
          - /url: /trading
        - link "工作流" [ref=e28] [cursor=pointer]:
          - /url: /workflows
        - link "日志" [ref=e29] [cursor=pointer]:
          - /url: /logs
        - link "配置" [ref=e30] [cursor=pointer]:
          - /url: /settings
      - generic [ref=e31]:
        - generic [ref=e32]:
          - generic [ref=e33]: BTC
          - generic [ref=e34]: —
          - generic [ref=e35]: —
        - generic [ref=e36]:
          - generic [ref=e37]: ETH
          - generic [ref=e38]: —
          - generic [ref=e39]: —
        - generic [ref=e40]:
          - generic [ref=e41]: SOL
          - generic [ref=e42]: —
          - generic [ref=e43]: —
        - button "通知" [ref=e45] [cursor=pointer]:
          - img [ref=e46]
        - generic [ref=e51]: 实时运行中
        - button "收起右侧面板" [ref=e52] [cursor=pointer]:
          - img [ref=e53]
    - generic [ref=e56]:
      - generic [ref=e58]: 智能体集群
      - link "工作流 历史" [ref=e60] [cursor=pointer]:
        - /url: /workflows
        - generic [ref=e62]: 工作流
        - generic [ref=e63]: 历史
      - generic [ref=e65]:
        - generic [ref=e66]: 账户概况
        - generic [ref=e67]:
          - generic [ref=e68]: 总资产
          - generic [ref=e69]: $
        - generic [ref=e70]:
          - generic [ref=e71]: 今日盈亏
          - generic [ref=e72]: $
        - generic [ref=e74]: 活跃订单
        - generic [ref=e76]: 今日订单
        - generic [ref=e78]: 账户数
      - generic [ref=e80]:
        - generic [ref=e81]: 活跃策略
        - generic [ref=e83]: 即将上线
    - generic [ref=e84]:
      - generic [ref=e85]:
        - generic [ref=e86]:
          - heading "回测记录" [level=1] [ref=e87]
          - paragraph [ref=e88]: 查看历史回测结果与详细分析
        - button "+ 新建策略" [ref=e89] [cursor=pointer]
      - generic [ref=e90]:
        - textbox "搜索策略名 / 品种..." [ref=e92]
        - combobox [ref=e93] [cursor=pointer]:
          - option "全部类型" [selected]
          - option "网格搜索"
          - option "单次回测"
      - table [ref=e95]:
        - rowgroup [ref=e96]:
          - row "类型 策略 品种 周期 回测周期 收益率 夏普 回撤 交易数 参数 (前3) 操作" [ref=e97]:
            - columnheader [ref=e98]
            - columnheader "类型" [ref=e99]
            - columnheader "策略" [ref=e100]
            - columnheader "品种" [ref=e101]
            - columnheader "周期" [ref=e102]
            - columnheader "回测周期" [ref=e103]
            - columnheader "收益率" [ref=e104]
            - columnheader "夏普" [ref=e105]
            - columnheader "回撤" [ref=e106]
            - columnheader "交易数" [ref=e107]
            - columnheader "参数 (前3)" [ref=e108]
            - columnheader "操作" [ref=e109]
        - rowgroup [ref=e110]:
          - row "▶ 网格 grid-alpha BTCUSDT 1h — +12.50% 1.80 40/40 — — 🔍 详情" [ref=e111] [cursor=pointer]:
            - cell "▶" [ref=e112]
            - cell "网格" [ref=e113]
            - cell "grid-alpha" [ref=e114]
            - cell "BTCUSDT" [ref=e115]
            - cell "1h" [ref=e116]
            - cell "—" [ref=e117]
            - cell "+12.50%" [ref=e118]
            - cell "1.80" [ref=e119]
            - cell "40/40" [ref=e120]
            - cell "—" [ref=e121]
            - cell "—" [ref=e122]
            - cell "🔍 详情" [ref=e123]:
              - link "🔍 详情" [ref=e124]:
                - /url: /settings?tab=grid-search
          - 'row "最佳收益: +12.50% | 最佳夏普: 1.80 | 完成: 40/40 | 2026/09/01" [ref=e125]':
            - 'cell "最佳收益: +12.50% | 最佳夏普: 1.80 | 完成: 40/40 | 2026/09/01" [ref=e126]'
          - row "单次 strat-100 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +85.00% 11.00 +5.00% 10 fast=100, slow=110, threshold=0.01 详情" [ref=e127]:
            - cell [ref=e128]
            - cell "单次" [ref=e129]
            - cell "strat-100" [ref=e130]
            - cell "BTCUSDT" [ref=e131]
            - cell "1h" [ref=e132]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e133]
            - cell "+85.00%" [ref=e134]
            - cell "11.00" [ref=e135]
            - cell "+5.00%" [ref=e136]
            - cell "10" [ref=e137]
            - cell "fast=100, slow=110, threshold=0.01" [ref=e138]
            - cell "详情" [ref=e139]:
              - link "详情" [ref=e140] [cursor=pointer]:
                - /url: /backtest/r-100
          - row "单次 strat-101 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +86.00% 11.10 +5.00% 10 fast=101, slow=111, threshold=0.01 详情" [ref=e141]:
            - cell [ref=e142]
            - cell "单次" [ref=e143]
            - cell "strat-101" [ref=e144]
            - cell "BTCUSDT" [ref=e145]
            - cell "1h" [ref=e146]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e147]
            - cell "+86.00%" [ref=e148]
            - cell "11.10" [ref=e149]
            - cell "+5.00%" [ref=e150]
            - cell "10" [ref=e151]
            - cell "fast=101, slow=111, threshold=0.01" [ref=e152]
            - cell "详情" [ref=e153]:
              - link "详情" [ref=e154] [cursor=pointer]:
                - /url: /backtest/r-101
          - row "单次 strat-102 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +87.00% 11.20 +5.00% 10 fast=102, slow=112, threshold=0.01 详情" [ref=e155]:
            - cell [ref=e156]
            - cell "单次" [ref=e157]
            - cell "strat-102" [ref=e158]
            - cell "BTCUSDT" [ref=e159]
            - cell "1h" [ref=e160]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e161]
            - cell "+87.00%" [ref=e162]
            - cell "11.20" [ref=e163]
            - cell "+5.00%" [ref=e164]
            - cell "10" [ref=e165]
            - cell "fast=102, slow=112, threshold=0.01" [ref=e166]
            - cell "详情" [ref=e167]:
              - link "详情" [ref=e168] [cursor=pointer]:
                - /url: /backtest/r-102
          - row "单次 strat-103 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +88.00% 11.30 +5.00% 10 fast=103, slow=113, threshold=0.01 详情" [ref=e169]:
            - cell [ref=e170]
            - cell "单次" [ref=e171]
            - cell "strat-103" [ref=e172]
            - cell "BTCUSDT" [ref=e173]
            - cell "1h" [ref=e174]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e175]
            - cell "+88.00%" [ref=e176]
            - cell "11.30" [ref=e177]
            - cell "+5.00%" [ref=e178]
            - cell "10" [ref=e179]
            - cell "fast=103, slow=113, threshold=0.01" [ref=e180]
            - cell "详情" [ref=e181]:
              - link "详情" [ref=e182] [cursor=pointer]:
                - /url: /backtest/r-103
          - row "单次 strat-104 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +89.00% 11.40 +5.00% 10 fast=104, slow=114, threshold=0.01 详情" [ref=e183]:
            - cell [ref=e184]
            - cell "单次" [ref=e185]
            - cell "strat-104" [ref=e186]
            - cell "BTCUSDT" [ref=e187]
            - cell "1h" [ref=e188]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e189]
            - cell "+89.00%" [ref=e190]
            - cell "11.40" [ref=e191]
            - cell "+5.00%" [ref=e192]
            - cell "10" [ref=e193]
            - cell "fast=104, slow=114, threshold=0.01" [ref=e194]
            - cell "详情" [ref=e195]:
              - link "详情" [ref=e196] [cursor=pointer]:
                - /url: /backtest/r-104
          - row "单次 strat-105 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +90.00% 11.50 +5.00% 10 fast=105, slow=115, threshold=0.01 详情" [ref=e197]:
            - cell [ref=e198]
            - cell "单次" [ref=e199]
            - cell "strat-105" [ref=e200]
            - cell "BTCUSDT" [ref=e201]
            - cell "1h" [ref=e202]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e203]
            - cell "+90.00%" [ref=e204]
            - cell "11.50" [ref=e205]
            - cell "+5.00%" [ref=e206]
            - cell "10" [ref=e207]
            - cell "fast=105, slow=115, threshold=0.01" [ref=e208]
            - cell "详情" [ref=e209]:
              - link "详情" [ref=e210] [cursor=pointer]:
                - /url: /backtest/r-105
          - row "单次 strat-106 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +91.00% 11.60 +5.00% 10 fast=106, slow=116, threshold=0.01 详情" [ref=e211]:
            - cell [ref=e212]
            - cell "单次" [ref=e213]
            - cell "strat-106" [ref=e214]
            - cell "BTCUSDT" [ref=e215]
            - cell "1h" [ref=e216]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e217]
            - cell "+91.00%" [ref=e218]
            - cell "11.60" [ref=e219]
            - cell "+5.00%" [ref=e220]
            - cell "10" [ref=e221]
            - cell "fast=106, slow=116, threshold=0.01" [ref=e222]
            - cell "详情" [ref=e223]:
              - link "详情" [ref=e224] [cursor=pointer]:
                - /url: /backtest/r-106
          - row "单次 strat-107 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +92.00% 11.70 +5.00% 10 fast=107, slow=117, threshold=0.01 详情" [ref=e225]:
            - cell [ref=e226]
            - cell "单次" [ref=e227]
            - cell "strat-107" [ref=e228]
            - cell "BTCUSDT" [ref=e229]
            - cell "1h" [ref=e230]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e231]
            - cell "+92.00%" [ref=e232]
            - cell "11.70" [ref=e233]
            - cell "+5.00%" [ref=e234]
            - cell "10" [ref=e235]
            - cell "fast=107, slow=117, threshold=0.01" [ref=e236]
            - cell "详情" [ref=e237]:
              - link "详情" [ref=e238] [cursor=pointer]:
                - /url: /backtest/r-107
          - row "单次 strat-108 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +93.00% 11.80 +5.00% 10 fast=108, slow=118, threshold=0.01 详情" [ref=e239]:
            - cell [ref=e240]
            - cell "单次" [ref=e241]
            - cell "strat-108" [ref=e242]
            - cell "BTCUSDT" [ref=e243]
            - cell "1h" [ref=e244]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e245]
            - cell "+93.00%" [ref=e246]
            - cell "11.80" [ref=e247]
            - cell "+5.00%" [ref=e248]
            - cell "10" [ref=e249]
            - cell "fast=108, slow=118, threshold=0.01" [ref=e250]
            - cell "详情" [ref=e251]:
              - link "详情" [ref=e252] [cursor=pointer]:
                - /url: /backtest/r-108
          - row "单次 strat-109 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +94.00% 11.90 +5.00% 10 fast=109, slow=119, threshold=0.01 详情" [ref=e253]:
            - cell [ref=e254]
            - cell "单次" [ref=e255]
            - cell "strat-109" [ref=e256]
            - cell "BTCUSDT" [ref=e257]
            - cell "1h" [ref=e258]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e259]
            - cell "+94.00%" [ref=e260]
            - cell "11.90" [ref=e261]
            - cell "+5.00%" [ref=e262]
            - cell "10" [ref=e263]
            - cell "fast=109, slow=119, threshold=0.01" [ref=e264]
            - cell "详情" [ref=e265]:
              - link "详情" [ref=e266] [cursor=pointer]:
                - /url: /backtest/r-109
          - row "单次 strat-110 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +95.00% 12.00 +5.00% 10 fast=110, slow=120, threshold=0.01 详情" [ref=e267]:
            - cell [ref=e268]
            - cell "单次" [ref=e269]
            - cell "strat-110" [ref=e270]
            - cell "BTCUSDT" [ref=e271]
            - cell "1h" [ref=e272]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e273]
            - cell "+95.00%" [ref=e274]
            - cell "12.00" [ref=e275]
            - cell "+5.00%" [ref=e276]
            - cell "10" [ref=e277]
            - cell "fast=110, slow=120, threshold=0.01" [ref=e278]
            - cell "详情" [ref=e279]:
              - link "详情" [ref=e280] [cursor=pointer]:
                - /url: /backtest/r-110
          - row "单次 strat-111 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +96.00% 12.10 +5.00% 10 fast=111, slow=121, threshold=0.01 详情" [ref=e281]:
            - cell [ref=e282]
            - cell "单次" [ref=e283]
            - cell "strat-111" [ref=e284]
            - cell "BTCUSDT" [ref=e285]
            - cell "1h" [ref=e286]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e287]
            - cell "+96.00%" [ref=e288]
            - cell "12.10" [ref=e289]
            - cell "+5.00%" [ref=e290]
            - cell "10" [ref=e291]
            - cell "fast=111, slow=121, threshold=0.01" [ref=e292]
            - cell "详情" [ref=e293]:
              - link "详情" [ref=e294] [cursor=pointer]:
                - /url: /backtest/r-111
          - row "单次 strat-112 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +97.00% 12.20 +5.00% 10 fast=112, slow=122, threshold=0.01 详情" [ref=e295]:
            - cell [ref=e296]
            - cell "单次" [ref=e297]
            - cell "strat-112" [ref=e298]
            - cell "BTCUSDT" [ref=e299]
            - cell "1h" [ref=e300]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e301]
            - cell "+97.00%" [ref=e302]
            - cell "12.20" [ref=e303]
            - cell "+5.00%" [ref=e304]
            - cell "10" [ref=e305]
            - cell "fast=112, slow=122, threshold=0.01" [ref=e306]
            - cell "详情" [ref=e307]:
              - link "详情" [ref=e308] [cursor=pointer]:
                - /url: /backtest/r-112
          - row "单次 strat-113 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +98.00% 12.30 +5.00% 10 fast=113, slow=123, threshold=0.01 详情" [ref=e309]:
            - cell [ref=e310]
            - cell "单次" [ref=e311]
            - cell "strat-113" [ref=e312]
            - cell "BTCUSDT" [ref=e313]
            - cell "1h" [ref=e314]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e315]
            - cell "+98.00%" [ref=e316]
            - cell "12.30" [ref=e317]
            - cell "+5.00%" [ref=e318]
            - cell "10" [ref=e319]
            - cell "fast=113, slow=123, threshold=0.01" [ref=e320]
            - cell "详情" [ref=e321]:
              - link "详情" [ref=e322] [cursor=pointer]:
                - /url: /backtest/r-113
          - row "单次 strat-114 BTCUSDT 1h 2026/08/01 ~ 2026/08/31 +99.00% 12.40 +5.00% 10 fast=114, slow=124, threshold=0.01 详情" [ref=e323]:
            - cell [ref=e324]
            - cell "单次" [ref=e325]
            - cell "strat-114" [ref=e326]
            - cell "BTCUSDT" [ref=e327]
            - cell "1h" [ref=e328]
            - cell "2026/08/01 ~ 2026/08/31" [ref=e329]
            - cell "+99.00%" [ref=e330]
            - cell "12.40" [ref=e331]
            - cell "+5.00%" [ref=e332]
            - cell "10" [ref=e333]
            - cell "fast=114, slow=124, threshold=0.01" [ref=e334]
            - cell "详情" [ref=e335]:
              - link "详情" [ref=e336] [cursor=pointer]:
                - /url: /backtest/r-114
    - generic [ref=e338]:
      - generic [ref=e339]:
        - generic [ref=e340]: 当前持仓
        - generic [ref=e341]: 暂无持仓
      - generic [ref=e342]:
        - generic [ref=e343]: 风险仪表盘
        - generic [ref=e344]:
          - generic [ref=e345]:
            - generic [ref=e346]: P0 事件
            - generic [ref=e347]: "0"
          - generic [ref=e348]:
            - generic [ref=e349]: P1 事件
            - generic [ref=e350]: "0"
          - generic [ref=e351]:
            - generic [ref=e352]: 总事件
            - generic [ref=e353]: "0"
          - generic [ref=e354]:
            - generic [ref=e355]: 已解决
            - generic [ref=e356]: "0"
      - generic [ref=e357]:
        - generic [ref=e358]: 近期执行
        - generic [ref=e359]: 暂无成交记录
      - generic [ref=e360]:
        - generic [ref=e361]: 模型调用统计
        - generic [ref=e362]:
          - generic [ref=e363]:
            - generic [ref=e364]: 今日 API 调用
            - generic [ref=e365]: "4"
          - generic [ref=e366]:
            - generic [ref=e367]: 平均响应时间
            - generic [ref=e368]: 124ms
          - generic [ref=e369]:
            - generic [ref=e370]: Token 消耗
            - generic [ref=e371]: 1.2M
          - generic [ref=e372]:
            - generic [ref=e373]: 推理成功率
            - generic [ref=e374]: 99.7%
    - button "打开 Supervisor 对话" [ref=e375] [cursor=pointer]:
      - img [ref=e376]
```

# Test source

```ts
  28  |     final_capital: "11000",
  29  |     total_return_pct: i - 15,
  30  |     sharpe_ratio: 1 + i * 0.1,
  31  |     max_drawdown_pct: 5,
  32  |     win_rate: 0.5,
  33  |     total_trades: 10,
  34  |     git_commit_hash: "abc",
  35  |     parameters: { fast: i, slow: i + 10, threshold: 0.01 },
  36  |     metrics: { sortino: 1.1, calmar: 0.8, profit_factor: 1.5 },
  37  |     review_status: "pending",
  38  |     review_notes: "",
  39  |     reviewed_at: null,
  40  |     created_at: "2026-09-01T00:00:00Z",
  41  |   };
  42  | }
  43  | 
  44  | const GROUPS: {
  45  |   groups: BacktestGroup[];
  46  |   group_count: number;
  47  |   total_records: number;
  48  |   num_pages: number;
  49  |   current_page: number;
  50  | } = {
  51  |   groups: [
  52  |     {
  53  |       type: "grid_search",
  54  |       job_id: "job-1",
  55  |       job_name: "grid-alpha",
  56  |       symbol: "BTCUSDT",
  57  |       timeframe: "1h",
  58  |       status: "completed",
  59  |       total_combinations: 40,
  60  |       completed: 40,
  61  |       best_return_pct: 12.5,
  62  |       best_sharpe: 1.8,
  63  |       created_at: "2026-09-01T00:00:00Z",
  64  |       results: Array.from({ length: 40 }, (_, i) => makeResult(i)),
  65  |     },
  66  |     ...Array.from({ length: 15 }, (_, i): BacktestGroup => ({
  67  |       type: "single",
  68  |       symbol: "ETHUSDT",
  69  |       timeframe: "4h",
  70  |       created_at: "2026-09-02T00:00:00Z",
  71  |       result: makeResult(100 + i),
  72  |     })),
  73  |   ],
  74  |   group_count: 16,
  75  |   total_records: 55,
  76  |   num_pages: 1,
  77  |   current_page: 1,
  78  | };
  79  | 
  80  | // 主内容滚动容器（DashboardShell 中间列，pt-3 pb-16 为其特征类）
  81  | const MAIN = "div.pt-3.pb-16";
  82  | 
  83  | async function wheelScrolls(page: Page, name: string) {
  84  |   // 鼠标停在主内容区中间（主列 x∈[220,1000], y∈[52,720]）
  85  |   await page.mouse.move(610, 386);
  86  |   const before = await page.locator(MAIN).evaluate((el) => el.scrollTop);
  87  |   await page.mouse.wheel(0, 600);
  88  |   await page.waitForTimeout(200);
  89  |   const after = await page.locator(MAIN).evaluate((el) => el.scrollTop);
  90  |   expect(after, `${name}：滚轮应能滚动主内容区`).toBeGreaterThan(before);
  91  | }
  92  | 
  93  | test("回测记录页展开网格组后仍可滚动", async ({ page }) => {
  94  |   await page.route("**/api/**", (route) => {
  95  |     const url = route.request().url();
  96  |     if (url.includes("/api/auth/me")) {
  97  |       return route.fulfill({
  98  |         status: 200,
  99  |         contentType: "application/json",
  100 |         body: JSON.stringify({ username: "admin", email: "admin@tradeclaw.local" }),
  101 |       });
  102 |     }
  103 |     if (url.includes("/api/backtest/results")) {
  104 |       return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GROUPS) });
  105 |     }
  106 |     if (url.includes("/api/agent/list")) {
  107 |       return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ agents: [] }) });
  108 |     }
  109 |     if (url.includes("/api/trading/orders") || url.includes("/api/risk/events")) {
  110 |       return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  111 |     }
  112 |     if (url.includes("/api/notify/unread-count")) {
  113 |       return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ unread_count: 0 }) });
  114 |     }
  115 |     return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  116 |   });
  117 |   await page.addInitScript(() => {
  118 |     localStorage.setItem("tradeclaw_access", "test-token");
  119 |   });
  120 | 
  121 |   await page.goto("/backtest");
  122 |   await expect(page.getByText("grid-alpha")).toBeVisible();
  123 | 
  124 |   // 对照：展开前（单次行已撑高页面）滚动正常
  125 |   const preOverflow = await page
  126 |     .locator(MAIN)
  127 |     .evaluate((el) => el.scrollHeight - el.clientHeight);
> 128 |   expect(preOverflow, "展开前内容应超出视口").toBeGreaterThan(100);
      |                                     ^ Error: 展开前内容应超出视口
  129 |   await wheelScrolls(page, "展开前");
  130 | 
  131 |   // 展开网格组
  132 |   await page.locator("tr", { hasText: "grid-alpha" }).first().click();
  133 |   await expect(page.getByText("策略参数").first()).toBeVisible();
  134 |   expect(await page.getByText("策略参数").count()).toBe(40);
  135 | 
  136 |   // 用户症状：展开后不能滚动
  137 |   const postOverflow = await page
  138 |     .locator(MAIN)
  139 |     .evaluate((el) => el.scrollHeight - el.clientHeight);
  140 |   expect(postOverflow, "展开后内容应超出视口").toBeGreaterThan(500);
  141 |   await wheelScrolls(page, "展开后");
  142 | });
  143 | 
```