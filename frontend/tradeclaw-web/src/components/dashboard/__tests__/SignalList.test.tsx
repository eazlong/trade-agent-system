import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import SignalList from "../SignalList";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  signalMonitorApi: {
    list: vi.fn(),
  },
}));

const mockSignals = {
  success: true,
  data: [
    {
      id: "sig-1",
      name: "RSI突破",
      symbol: "BTC/USDT",
      interval: "1h",
      source: "binance",
      indicator_type: "RSI",
      indicator_params: { period: 14 },
      condition: { type: "cross_over", value: 70 },
      trigger_type: "continuous" as const,
      action_type: "notify" as const,
      action_params: {},
      status: "active" as const,
      last_triggered_at: "2026-04-28T09:42:00Z",
      trigger_count: 5,
      expires_at: null,
      created_at: "2026-04-28T08:00:00Z",
      updated_at: "2026-04-28T09:42:00Z",
    },
  ],
};

describe("SignalList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows empty state when no signals", async () => {
    vi.spyOn(api.signalMonitorApi, "list").mockResolvedValue({
      success: true,
      data: [],
    });
    render(<SignalList />);
    expect(await screen.findByText("暂无活跃信号")).toBeTruthy();
  });

  it("renders active signals from API", async () => {
    vi.spyOn(api.signalMonitorApi, "list").mockResolvedValue(mockSignals);
    render(<SignalList />);
    expect(await screen.findByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("RSI · RSI突破")).toBeTruthy();
  });

  it("filters out disabled/expired signals", async () => {
    vi.spyOn(api.signalMonitorApi, "list").mockResolvedValue({
      success: true,
      data: [
        { ...mockSignals.data[0], status: "disabled" as const, id: "sig-2" },
      ],
    });
    render(<SignalList />);
    expect(await screen.findByText("暂无活跃信号")).toBeTruthy();
  });

  it("displays error on API failure", async () => {
    vi.spyOn(api.signalMonitorApi, "list").mockRejectedValue(
      new Error("Service unavailable")
    );
    render(<SignalList />);
    expect(await screen.findByText(/Service unavailable/)).toBeTruthy();
  });
});
