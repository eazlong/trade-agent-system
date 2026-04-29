import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import LogList from "../LogList";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  loggingApi: {
    getLogs: vi.fn(),
  },
}));

const mockLogs = {
  count: 2,
  page: 1,
  page_size: 20,
  has_next: false,
  results: [
    {
      id: "log-1",
      level: "INFO" as const,
      module: "agent.supervisor",
      logger_name: "supervisor",
      message: "任务路由完成 · 市场分析",
      trace_id: "trace-abc",
      extra_data: {},
      created_at: "2026-04-28T09:42:00Z",
    },
    {
      id: "log-2",
      level: "WARNING" as const,
      module: "risk.engine",
      logger_name: "risk",
      message: "仓位上限预警 · BTC 敞口 HIGH",
      trace_id: "trace-def",
      extra_data: {},
      created_at: "2026-04-28T09:40:00Z",
    },
  ],
};

describe("LogList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows skeleton while loading", () => {
    vi.spyOn(api.loggingApi, "getLogs").mockReturnValue(new Promise(() => {}));
    render(<LogList />);
    const skeleton = document.querySelector(".animate-pulse");
    expect(skeleton).toBeTruthy();
  });

  it("renders logs from API", async () => {
    vi.spyOn(api.loggingApi, "getLogs").mockResolvedValue(mockLogs);
    render(<LogList />);
    expect(await screen.findByText("agent.supervisor")).toBeTruthy();
    expect(screen.getByText("risk.engine")).toBeTruthy();
    expect(screen.getByText("任务路由完成 · 市场分析")).toBeTruthy();
  });

  it("displays error on API failure", async () => {
    vi.spyOn(api.loggingApi, "getLogs").mockRejectedValue(
      new Error("Log service down")
    );
    render(<LogList />);
    expect(await screen.findByText(/Log service down/)).toBeTruthy();
  });
});
