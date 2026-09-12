import { test, expect, type Page } from "@playwright/test";
import type { BacktestGroup, BacktestResult } from "../src/lib/api";

/**
 * 回测记录页：展开网格组后，主内容区应仍可滚动。
 *
 * 回归背景：DashboardShell 主列是固定高度的 flex 滚动容器，表格包裹层
 * （overflow-hidden）作为 flex 子项曾被压缩到容器高度并被裁切，导致
 * scrollHeight 不增长、页面无法滚动（展开网格组后内容变高时最明显）。
 *
 * API 全部 mock（无需后端）：
 *  - /api/auth/me/            → 用户
 *  - /api/backtest/results/*  → 1 个 40 结果的网格组 + 15 个单次组
 *    （单次组保证展开前页面即可滚动，作为对照）
 *  - /api/agent/list 等       → 空数据
 */

function makeResult(i: number): BacktestResult {
  return {
    id: `r-${i}`,
    strategy: 1,
    strategy_name: `strat-${i}`,
    symbol: "BTCUSDT",
    timeframe: "1h",
    start_date: "2026-08-01",
    end_date: "2026-08-31",
    initial_capital: "10000",
    final_capital: "11000",
    total_return_pct: i - 15,
    sharpe_ratio: 1 + i * 0.1,
    max_drawdown_pct: 5,
    win_rate: 0.5,
    total_trades: 10,
    git_commit_hash: "abc",
    parameters: { fast: i, slow: i + 10, threshold: 0.01 },
    metrics: { sortino: 1.1, calmar: 0.8, profit_factor: 1.5 },
    review_status: "pending",
    review_notes: "",
    reviewed_at: null,
    created_at: "2026-09-01T00:00:00Z",
  };
}

const GROUPS: {
  groups: BacktestGroup[];
  group_count: number;
  total_records: number;
  num_pages: number;
  current_page: number;
} = {
  groups: [
    {
      type: "grid_search",
      job_id: "job-1",
      job_name: "grid-alpha",
      symbol: "BTCUSDT",
      timeframe: "1h",
      status: "completed",
      total_combinations: 40,
      completed: 40,
      best_return_pct: 12.5,
      best_sharpe: 1.8,
      created_at: "2026-09-01T00:00:00Z",
      results: Array.from({ length: 40 }, (_, i) => makeResult(i)),
    },
    ...Array.from({ length: 15 }, (_, i): BacktestGroup => ({
      type: "single",
      symbol: "ETHUSDT",
      timeframe: "4h",
      created_at: "2026-09-02T00:00:00Z",
      result: makeResult(100 + i),
    })),
  ],
  group_count: 16,
  total_records: 55,
  num_pages: 1,
  current_page: 1,
};

// 主内容滚动容器（DashboardShell 中间列，pt-3 pb-16 为其特征类）
const MAIN = "div.pt-3.pb-16";

async function wheelScrolls(page: Page, name: string) {
  // 鼠标停在主内容区中间（主列 x∈[220,1000], y∈[52,720]）
  await page.mouse.move(610, 386);
  const before = await page.locator(MAIN).evaluate((el) => el.scrollTop);
  await page.mouse.wheel(0, 600);
  await page.waitForTimeout(200);
  const after = await page.locator(MAIN).evaluate((el) => el.scrollTop);
  expect(after, `${name}：滚轮应能滚动主内容区`).toBeGreaterThan(before);
}

test("回测记录页展开网格组后仍可滚动", async ({ page }) => {
  await page.route("**/api/**", (route) => {
    const url = route.request().url();
    if (url.includes("/api/auth/me")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ username: "admin", email: "admin@tradeclaw.local" }),
      });
    }
    if (url.includes("/api/backtest/results")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GROUPS) });
    }
    if (url.includes("/api/agent/list")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ agents: [] }) });
    }
    if (url.includes("/api/trading/orders") || url.includes("/api/risk/events")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    if (url.includes("/api/notify/unread-count")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ unread_count: 0 }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.addInitScript(() => {
    localStorage.setItem("tradeclaw_access", "test-token");
  });

  await page.goto("/backtest");
  await expect(page.getByText("grid-alpha")).toBeVisible();

  // 对照：展开前（单次行已撑高页面）滚动正常
  const preOverflow = await page
    .locator(MAIN)
    .evaluate((el) => el.scrollHeight - el.clientHeight);
  expect(preOverflow, "展开前内容应超出视口").toBeGreaterThan(100);
  await wheelScrolls(page, "展开前");

  // 展开网格组
  await page.locator("tr", { hasText: "grid-alpha" }).first().click();
  await expect(page.getByText("策略参数").first()).toBeVisible();
  expect(await page.getByText("策略参数").count()).toBe(40);

  // 用户症状：展开后不能滚动
  const postOverflow = await page
    .locator(MAIN)
    .evaluate((el) => el.scrollHeight - el.clientHeight);
  expect(postOverflow, "展开后内容应超出视口").toBeGreaterThan(500);
  await wheelScrolls(page, "展开后");
});
