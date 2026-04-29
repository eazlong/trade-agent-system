import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import Metrics from "../Metrics";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  tradingApi: {
    getSummary: vi.fn(),
  },
}));

const mockSummary = {
  total_equity: "12847.50",
  today_realized_pnl: "34.20",
  active_orders_count: 3,
  total_orders_today: 12,
  account_count: 2,
};

describe("Metrics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows skeleton while loading", () => {
    vi.spyOn(api.tradingApi, "getSummary").mockReturnValue(
      new Promise(() => {})
    );
    render(<Metrics />);
    expect(screen.getAllByRole("generic").length).toBeGreaterThan(0);
  });

  it("renders trading summary on success", async () => {
    vi.spyOn(api.tradingApi, "getSummary").mockResolvedValue(mockSummary);
    render(<Metrics />);
    expect(await screen.findByText("12847.5000")).toBeTruthy();
    expect(screen.getAllByText(/\+34\.20/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("今日 12 单")).toBeTruthy();
  });

  it("shows negative PnL", async () => {
    vi.spyOn(api.tradingApi, "getSummary").mockResolvedValue({
      ...mockSummary,
      today_realized_pnl: "-15.50",
    });
    render(<Metrics />);
    const matches = await screen.findAllByText(/-15\.50/);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("displays error on API failure", async () => {
    vi.spyOn(api.tradingApi, "getSummary").mockRejectedValue(
      new Error("Network error")
    );
    render(<Metrics />);
    expect(await screen.findAllByText(/Network error/)).toHaveLength(3);
  });
});
