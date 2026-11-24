/**
 * `/regime` 读面的两维契约（#67）。
 *
 * 这一页必须能看出「牛市里的暴跌日」：`cycle` 与 `volatility` **同时成立、互不覆盖**。
 * 三条纪律在这里钉死：
 *
 *   1. **两维各自独立渲染**。写成 `cycle ?? volatility` 那种二选一，`bull + event_spike`
 *      那天就只会显示其中一句，而「牛市暴跌日」这个形态正是 #64/#65 要表达的。
 *   2. **`cycle === null` 时不填假结论**。旧结构里波动吃掉了周期，那天周期维就是空的；
 *      拿「横盘」填上会让读的人以为机制判出了方向。
 *   3. **来源的 display 原样转述后端**。前端不把 `proxy` 翻成中文——那句话里的
 *      「没有消息面确认」正是代理判据最容易被高估的地方，两处各写一份时改一处漏一处，
 *      就会让其中一个入口更乐观。
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  overview: null as unknown,
}));

vi.mock("@/components/layout/DashboardShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ isStaff: true }),
}));

vi.mock("@/hooks/useRegimeOverview", () => ({
  useRegimeOverview: () => ({
    data: state.overview,
    loading: false,
    error: null,
    refetch: () => {},
  }),
}));

vi.mock("@/lib/api", () => ({
  regimeApi: {
    getPool: () => Promise.resolve({ cells: [], columns: [], evidence: {} }),
    getGenerations: () => Promise.resolve({ results: [] }),
    getCellEvidence: () => Promise.resolve({ evidence: {} }),
  },
}));

vi.mock("@/components/regime/PoolTable", () => ({ default: () => null }));
vi.mock("@/components/regime/GenerationHistory", () => ({ default: () => null }));
vi.mock("@/components/regime/RebuildButton", () => ({ default: () => null }));
vi.mock("@/components/regime/StrategyLifecycleButton", () => ({ default: () => null }));
vi.mock("@/components/regime/SwitchCard", () => ({ default: () => null }));

import RegimePage from "../page";

function labelled(value: string, display: string) {
  return { value, display };
}

function overviewWith(regimeState: Record<string, unknown>) {
  return {
    switches: [],
    pool: { has_generation: false, display: "还没有任何一代池化表", generation: null },
    regime_state: {
      regime: null,
      cycle: null,
      volatility: null,
      volatility_source: null,
      effective_at: null,
      cold: true,
      display: "库中还没有任何生效过的判定（冷启动）",
      ...regimeState,
    },
  };
}

describe("/regime 页面的两维", () => {
  beforeEach(() => {
    state.overview = null;
  });

  it("把「牛市 + 事件波动」两句同时印出来", () => {
    state.overview = overviewWith({
      regime: labelled("high_vol", "高波动"),
      cycle: labelled("bull", "上行周期"),
      volatility: labelled("event_spike", "事件波动"),
      volatility_source: labelled(
        "proxy",
        "价格代理（跳空 / 放量 / ATR 骤升 / 单日大幅），没有消息面确认"
      ),
      cold: false,
    });
    render(<RegimePage />);

    expect(screen.getByTestId("regime-axis-cycle").textContent).toContain("上行周期");
    expect(screen.getByTestId("regime-axis-volatility").textContent).toContain("事件波动");
    // 两句都在 = 不是二选一。
    expect(screen.getByTestId("regime-axes").textContent).toContain("上行周期");
  });

  it("来源的 display 原样转述后端，前端不自己翻译", () => {
    state.overview = overviewWith({
      regime: labelled("high_vol", "高波动"),
      cycle: labelled("bull", "上行周期"),
      volatility: labelled("event_spike", "事件波动"),
      volatility_source: labelled(
        "proxy",
        "价格代理（跳空 / 放量 / ATR 骤升 / 单日大幅），没有消息面确认"
      ),
      cold: false,
    });
    render(<RegimePage />);

    const source = screen.getByTestId("regime-axis-volatility-source");
    expect(source.textContent).toContain("没有消息面确认");
    expect(source.textContent).not.toContain("proxy");
  });

  it("没有来源时不渲染那一行（不是「未知」占位）", () => {
    state.overview = overviewWith({
      regime: labelled("uptrend", "上行趋势"),
      cycle: labelled("bull", "上行周期"),
      volatility: labelled("normal", "无事件波动"),
      volatility_source: null,
      cold: false,
    });
    render(<RegimePage />);

    expect(screen.queryByTestId("regime-axis-volatility-source")).toBeNull();
    expect(screen.getByTestId("regime-axis-volatility").textContent).toContain(
      "无事件波动"
    );
  });

  it("cycle 为空时不填假结论，只印有值的那一维", () => {
    state.overview = overviewWith({
      regime: labelled("high_vol", "高波动"),
      cycle: null,
      volatility: labelled("event_spike", "事件波动"),
      volatility_source: labelled("proxy", "价格代理"),
      cold: false,
    });
    render(<RegimePage />);

    expect(screen.queryByTestId("regime-axis-cycle")).toBeNull();
    expect(screen.getByTestId("regime-axis-volatility").textContent).toContain("事件波动");
  });

  it("两维全空时整段不渲染（旧行的合法形态，不是错误）", () => {
    state.overview = overviewWith({
      regime: labelled("downtrend", "下行趋势"),
      cold: false,
    });
    render(<RegimePage />);

    expect(screen.queryByTestId("regime-axes")).toBeNull();
  });
});
