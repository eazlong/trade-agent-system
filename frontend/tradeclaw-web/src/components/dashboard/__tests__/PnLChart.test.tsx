import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import PnLChart from "../PnLChart";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  backtestApi: {
    getList: vi.fn(),
    getFullDetail: vi.fn(),
  },
}));

describe("PnLChart", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows skeleton while loading", () => {
    vi.spyOn(api.backtestApi, "getList").mockReturnValue(new Promise(() => {}));
    render(<PnLChart />);
    const skeleton = document.querySelector(".animate-pulse");
    expect(skeleton).toBeTruthy();
  });

  it("shows skeleton when no backtests", async () => {
    vi.useFakeTimers();
    vi.spyOn(api.backtestApi, "getList").mockResolvedValue({
      count: 0,
      num_pages: 0,
      current_page: 1,
      results: [],
    });
    render(<PnLChart />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    const skeleton = document.querySelector(".animate-pulse");
    expect(skeleton).toBeTruthy();
    vi.useRealTimers();
  });

  it("renders chart with equity curve data", async () => {
    vi.useFakeTimers();
    vi.spyOn(api.backtestApi, "getList").mockResolvedValue({
      count: 1,
      num_pages: 1,
      current_page: 1,
      results: [
        { id: "bt-1", strategy_name: "TestStrategy", symbol: "BTC/USDT", timeframe: "1h" } as any,
      ],
    });
    vi.spyOn(api.backtestApi, "getFullDetail").mockResolvedValue({
      id: "bt-1",
      equity_curve: [
        { timestamp: "2026-04-01T00:00:00Z", equity: 1.0, drawdown: 0 },
        { timestamp: "2026-04-02T00:00:00Z", equity: 1.05, drawdown: 0 },
        { timestamp: "2026-04-03T00:00:00Z", equity: 1.1, drawdown: 0 },
      ],
      drawdown_curve: [],
      ohlcv_data: [],
      indicator_data: {},
    } as any);

    render(<PnLChart />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    // Verify no error state is shown
    expect(screen.queryByText(/加载失败/)).toBeNull();
    vi.useRealTimers();
  });

  it("displays error on API failure", async () => {
    vi.spyOn(api.backtestApi, "getList").mockRejectedValue(
      new Error("Backtest service unavailable")
    );
    render(<PnLChart />);
    expect(await screen.findByText(/Backtest service unavailable/)).toBeTruthy();
  });
});
