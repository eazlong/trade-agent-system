import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import AgentCards from "../AgentCards";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  agentApi: {
    listAgents: vi.fn(),
  },
}));

const mockAgents = {
  agents: [
    {
      name: "supervisor",
      display_name: "主控编排",
      role: "任务调度 · 信号聚合",
      description: "核心编排器",
      status: "running" as const,
      tools: [],
      intent: "route",
      tag_color: "var(--color-purple)",
      tag_bg: "var(--color-purple-dim)",
    },
    {
      name: "analyst",
      display_name: "市场分析",
      role: "技术指标 · 形态识别",
      description: "技术分析Agent",
      status: "ready" as const,
      tools: [],
      intent: "analyze",
      tag_color: "var(--color-teal)",
      tag_bg: "var(--color-teal-dim)",
    },
  ],
};

describe("AgentCards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows skeleton while loading", () => {
    vi.spyOn(api.agentApi, "listAgents").mockReturnValue(new Promise(() => {}));
    render(<AgentCards />);
    const skeleton = document.querySelector(".animate-pulse");
    expect(skeleton).toBeTruthy();
  });

  it("renders agent cards from API", async () => {
    vi.spyOn(api.agentApi, "listAgents").mockResolvedValue(mockAgents);
    render(<AgentCards />);
    expect(await screen.findByText("主控编排")).toBeTruthy();
    expect(screen.getByText("市场分析")).toBeTruthy();
    expect(screen.getByText("任务调度 · 信号聚合")).toBeTruthy();
  });

  it("displays error on API failure", async () => {
    vi.spyOn(api.agentApi, "listAgents").mockRejectedValue(
      new Error("Auth failed")
    );
    render(<AgentCards />);
    expect(await screen.findByText(/Auth failed/)).toBeTruthy();
  });
});
