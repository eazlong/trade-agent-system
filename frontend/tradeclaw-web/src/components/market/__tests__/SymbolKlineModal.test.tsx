/**
 * K 线弹窗的契约。
 *
 * 风险只有一条主线：**图上的东西必须是接口给的那份**。
 *
 *   1. `symbol` / `interval` 逐字进请求参数 —— 后端 `apps/trading/views.py::klines`
 *      只认那六个周期（多给一个就 400），前端不许另造取值。
 *   2. 取数失败**如实报错**，不拿空图冒充"这个品种没数据"。
 *   3. 切周期会带新周期重取，且**旧周期的图不许留在新周期的名下**（`requestKey`
 *      一旦对不上就是加载态）。
 *
 * `TView` 用桩替换（lightweight-charts 在 jsdom 里跑不起来），只断言它收到什么。
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OHLCVPoint } from "@/lib/api";

const getKlines = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  tradingApi: { getKlines },
}));

vi.mock("@/components/backtest/TView", () => ({
  default: ({
    ohlcv,
    timeframe,
    loading,
    availableTimeframes = [],
    onTimeframeChange,
  }: {
    ohlcv: OHLCVPoint[];
    timeframe?: string;
    loading?: boolean;
    availableTimeframes?: string[];
    onTimeframeChange?: (tf: string) => void;
  }) => (
    <div
      data-testid="tview-stub"
      data-bars={String(ohlcv.length)}
      data-timeframe={timeframe ?? ""}
      data-loading={String(!!loading)}
    >
      {availableTimeframes.map((tf) => (
        <button key={tf} onClick={() => onTimeframeChange?.(tf)}>
          {tf}
        </button>
      ))}
      {ohlcv.map((p) => (
        <span key={p.timestamp} data-testid="tview-bar" data-close={p.close} />
      ))}
    </div>
  ),
}));

import SymbolKlineModal, {
  DEFAULT_INTERVAL,
  KLINE_INTERVALS,
  KLINE_LIMIT,
} from "../SymbolKlineModal";

// ── 夹具 ──

function bars(n: number, base = 560): OHLCVPoint[] {
  return Array.from({ length: n }, (_, i) => ({
    timestamp: `2026-11-05T0${i % 10}:00:00`,
    open: base + i,
    high: base + i + 1,
    low: base + i - 1,
    close: base + i,
    volume: 100,
  }));
}

beforeEach(() => {
  getKlines.mockResolvedValue({ ohlcv_data: bars(3) });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("K 线弹窗", () => {
  it("拿到接口的 K 线并交给 TView", async () => {
    render(<SymbolKlineModal symbol="BNB/USDT" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("tview-stub").getAttribute("data-bars")).toBe("3");
    });

    const closes = screen
      .getAllByTestId("tview-bar")
      .map((el) => el.getAttribute("data-close"));
    expect(closes).toEqual(["560", "561", "562"]);
  });

  it("symbol / interval / limit 逐字进请求参数", async () => {
    render(<SymbolKlineModal symbol="BNB/USDT" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(getKlines).toHaveBeenCalledWith("BNB/USDT", DEFAULT_INTERVAL, KLINE_LIMIT);
    });
  });

  it("可选周期与后端 KLINE_INTERVALS 逐字一致", () => {
    expect(KLINE_INTERVALS).toEqual(["1m", "5m", "15m", "1h", "4h", "1d"]);
  });

  it("切周期带新周期重取，且加载中不保留旧图", async () => {
    render(<SymbolKlineModal symbol="ETH/USDT" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("tview-stub").getAttribute("data-bars")).toBe("3");
    });

    // 第二跳挂住，观察"正在加载"这一态
    let release: (v: { ohlcv_data: OHLCVPoint[] }) => void = () => {};
    getKlines.mockImplementationOnce(
      () => new Promise((res) => { release = res; })
    );

    fireEvent.click(screen.getByText("4h"));

    await waitFor(() => {
      expect(getKlines).toHaveBeenLastCalledWith("ETH/USDT", "4h", KLINE_LIMIT);
    });

    const stubbed = screen.getByTestId("tview-stub");
    expect(stubbed.getAttribute("data-timeframe")).toBe("4h");
    expect(stubbed.getAttribute("data-loading")).toBe("true");
    expect(stubbed.getAttribute("data-bars")).toBe("0");

    release({ ohlcv_data: bars(2, 3000) });
    await waitFor(() => {
      expect(screen.getByTestId("tview-stub").getAttribute("data-bars")).toBe("2");
    });
  });

  it("接口失败如实报错，不拿空图当'没数据'", async () => {
    getKlines.mockRejectedValueOnce(new Error("failed to fetch OHLCV for BNB/USDT (1h)"));

    render(<SymbolKlineModal symbol="BNB/USDT" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("kline-error").textContent).toBe(
        "failed to fetch OHLCV for BNB/USDT (1h)"
      );
    });
    expect(screen.getByTestId("tview-stub").getAttribute("data-bars")).toBe("0");
  });

  it("点关闭按钮回调 onClose", () => {
    const onClose = vi.fn();
    render(<SymbolKlineModal symbol="BNB/USDT" onClose={onClose} />);

    fireEvent.click(screen.getByLabelText("关闭"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Esc 关闭", () => {
    const onClose = vi.fn();
    render(<SymbolKlineModal symbol="BNB/USDT" onClose={onClose} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
