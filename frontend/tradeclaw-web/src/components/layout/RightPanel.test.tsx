/**
 * 右侧边栏「当前持仓」的收益必须来自后端持仓接口，且必须确定（可复现）。
 *
 * 事故（用户报障）：侧边栏收益显示不对。根因 —— RightPanel.tsx 里
 *   const pnlPct = (Math.random() - 0.4) * 5;   // "Mock PnL since backend doesn't provide it directly"
 * 每次刷新都是新随机数；且持仓本身是用**本地成交流水**在客户端净额拼出来的
 * （computePositions），与交易所真实持仓无关。而该注释的前提早已过期：
 * 后端 /api/trading/positions/ 返回 mark_price 与 unrealized_pnl，
 * 交易页（trading/page.tsx）正是用它正确展示"未实现盈亏"。
 *
 * 本文件锁死三条契约：
 *   1. 收益 = 接口的 unrealized_pnl（逐字，经 formatPnl 格式化）
 *   2. 多次渲染必须稳定（杜绝随机数回归）
 *   3. 持仓来自接口而非本地成交流水；无持仓/框架未启动时如实提示，不编造数字
 */

import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi, beforeEach } from "vitest";
import type { PositionResponse } from "@/lib/api";

const positions = vi.hoisted(() => ({
  value: { positions: [], executor_running: true } as PositionResponse,
}));
const orders = vi.hoisted(() => ({ value: [] as unknown[] }));

vi.mock("@/hooks/usePositions", () => ({
  usePositions: () => ({
    positions: positions.value,
    loading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/hooks/useOrders", () => ({
  useOrders: () => ({ orders: orders.value, loading: false }),
}));

vi.mock("@/hooks/useRiskEvents", () => ({
  useRiskEvents: () => ({ events: [] }),
}));

import RightPanel from "./RightPanel";

beforeEach(() => {
  positions.value = { positions: [], executor_running: true };
  orders.value = [];
});

afterEach(cleanup);

function pos(over: Partial<PositionResponse["positions"][number]> = {}) {
  return {
    exchange: "binance",
    symbol: "BTC/USDT",
    side: "long" as const,
    quantity: "0.5",
    entry_price: "60000",
    mark_price: "61000",
    unrealized_pnl: "123.45",
    ...over,
  };
}

it("收益取自接口的 unrealized_pnl（逐字），而非随机数", () => {
  positions.value = { positions: [pos()], executor_running: true };

  render(<RightPanel />);

  expect(screen.getByTestId("position-pnl").textContent).toBe("+123.45");
});

it("亏损持仓显示负号与红色", () => {
  positions.value = {
    positions: [pos({ side: "short", unrealized_pnl: "-50.5" })],
    executor_running: true,
  };

  render(<RightPanel />);

  const el = screen.getByTestId("position-pnl");
  expect(el.textContent).toBe("-50.50");
  expect(el.className).toContain("text-red");
});

it("多次渲染收益稳定（防止重新引入随机数）", () => {
  positions.value = { positions: [pos()], executor_running: true };

  const seen: string[] = [];
  for (let i = 0; i < 3; i++) {
    const { unmount } = render(<RightPanel />);
    seen.push(screen.getByTestId("position-pnl").textContent || "");
    unmount();
  }

  expect(new Set(seen).size).toBe(1);
  expect(seen[0]).toBe("+123.45");
});

it("持仓来自接口，不用本地成交流水在客户端拼", () => {
  // 成交流水里有 ADA 的买入，但接口只返回 ETH 持仓 → 侧边栏不得出现 ADA
  orders.value = [
    {
      symbol: "ADA/USDT",
      side: "buy",
      filled_quantity: "1000",
      avg_fill_price: "0.5",
      status: "filled",
      created_at: new Date().toISOString(),
    },
  ];
  positions.value = {
    positions: [pos({ symbol: "ETH/USDT", quantity: "2", unrealized_pnl: "10.00" })],
    executor_running: true,
  };

  render(<RightPanel />);

  expect(screen.getByText("ETH")).toBeDefined();
  expect(screen.queryByText("ADA")).toBeNull();
});

it("无持仓时如实提示，不编造数字", () => {
  positions.value = { positions: [], executor_running: true };

  render(<RightPanel />);

  expect(screen.getByText("暂无持仓")).toBeDefined();
  expect(screen.queryByTestId("position-pnl")).toBeNull();
});

it("交易框架未启动时给出后端提示，不显示收益", () => {
  positions.value = {
    positions: [],
    executor_running: false,
    message: "交易框架未启动，持仓数据暂不可用",
  };

  render(<RightPanel />);

  expect(screen.getByText("交易框架未启动，持仓数据暂不可用")).toBeDefined();
  expect(screen.queryByTestId("position-pnl")).toBeNull();
});

it("模型调用统计不显示任何编造的数字", () => {
  render(<RightPanel />);

  expect(screen.getByText("模型调用统计")).toBeDefined();
  // 曾经硬编码 / 随机生成的假值一律不得出现
  expect(screen.queryByText("124ms")).toBeNull();
  expect(screen.queryByText("1.2M")).toBeNull();
  expect(screen.queryByText("99.7%")).toBeNull();

  const values = screen
    .getAllByTestId("model-stat-value")
    .map((el) => el.textContent);
  expect(values).toEqual(["—", "—", "—", "—"]);
});

it("模型统计区标注未接入，且多次渲染完全一致", () => {
  const snapshots: string[] = [];
  let badgeSeen = false;
  for (let i = 0; i < 3; i++) {
    const { unmount } = render(<RightPanel />);
    snapshots.push(
      screen
        .getAllByTestId("model-stat-value")
        .map((el) => el.textContent)
        .join("|")
    );
    badgeSeen = badgeSeen || screen.getAllByText("未接入").length > 0;
    unmount();
  }

  expect(new Set(snapshots).size).toBe(1);
  expect(snapshots[0]).toBe("—|—|—|—");
  expect(badgeSeen).toBe(true);
});
