/**
 * 顶栏行情条的契约：**按用户的币种偏好画档 + 点开即 K 线**。
 *
 * 这一块的风险全在「画的是不是用户配的那几档」「点了之后拿到的是不是这个品种」：
 *
 *   1. 行情条是每页常驻的组件，品种少一个 / 多一个 / 价格口径搞错，页面上一眼看不出来
 *      ——所以夹具把报价钉成具体数字，并逐档断言。
 *   2. **档位来自偏好而不是常量**（`GET /api/auth/preferences/`）。用户把顶栏从默认四档
 *      改成 `[BTC, ETH, DOGE]` 之后，页面必须只画这三档，且此后只按这三档取 ticker。
 *   3. 点开必须**带上被点那一条自己的 symbol**，不是某个默认品种。这里点 BNB，断言弹窗
 *      收到的就是 BNB/USDT —— 传错的话图上画的是别的币，人不会发现。
 *   4. 没点开时不该有弹窗（图表库不该被无谓拉起）。
 *
 * 两条降级也钉住：偏好取不到时**退回默认四档继续跑**（顶栏不该因为一次配置读取失败而
 * 空掉——那会让「配置读失败」看起来像「行情全挂了」）；localStorage 缓存**只用来少闪
 * 一帧**，且不合规（条数不对）的缓存一律不用。
 *
 * `next/dynamic` 换成同步桩：测试只关心「点了谁、开了没有」，不关心懒加载的 chunk 何时
 * resolve。真实组件的取数行为另有 `SymbolKlineModal.test.tsx`。
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Ticker } from "@/lib/api";

const ticker = vi.hoisted(() => vi.fn());
const unreadCount = vi.hoisted(() => vi.fn());
const headerSymbols = vi.hoisted(() => vi.fn());
const saveHeaderSymbols = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: () => "/overview",
}));

vi.mock("@/components/layout/DashboardShell", () => ({
  useRightPanel: () => ({ show: false, toggle: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  marketApi: { ticker },
  notificationApi: { unreadCount, list: vi.fn(), markAllRead: vi.fn() },
  preferencesApi: { headerSymbols, saveHeaderSymbols },
}));

vi.mock("next/dynamic", () => ({
  default: () =>
    function KlineStub({ symbol, onClose }: { symbol: string; onClose: () => void }) {
      return (
        <div data-testid="kline-stub" data-symbol={symbol}>
          <button onClick={onClose} data-testid="kline-stub-close">
            close
          </button>
        </div>
      );
    },
}));

import Topbar from "../Topbar";
import { DEFAULT_HEADER_SYMBOLS } from "@/lib/headerSymbols";

// ── 夹具 ──

const PRICES: Record<string, number> = {
  "BTC/USDT": 68000,
  "ETH/USDT": 3200,
  "SOL/USDT": 150.5,
  "BNB/USDT": 560.25,
  "DOGE/USDT": 0.4123,
};

function tickerOf(symbol: string): Ticker {
  const last = PRICES[symbol];
  return {
    symbol,
    last_price: last,
    bid_price: last,
    bid_quantity: 1,
    ask_price: last,
    ask_quantity: 1,
    high_24h: last,
    low_24h: last,
    volume_24h: 1,
    turnover_24h: 1,
    change_24h: 1,
    change_pct_24h: 1.5,
    timestamp: "2026-11-05T00:00:00Z",
    source: "binance",
  };
}

/** 偏好接口的响应形状（与后端 `_header_symbols_payload` 一致）。 */
function prefOf(symbols: string[]) {
  return {
    header_symbols: symbols,
    is_default: symbols.join(",") === DEFAULT_HEADER_SYMBOLS.join(","),
    min: 3,
    max: 5,
    default: DEFAULT_HEADER_SYMBOLS,
  };
}

/** ticker 被问过的品种（按调用顺序，去重前）。 */
function symbolsAsked(): string[] {
  return ticker.mock.calls.map(
    (call: unknown[]) => (call[0] as { symbol: string }).symbol
  );
}

/**
 * 挂载顶栏。
 *
 * 挂载会立刻发两类异步请求（偏好 GET + 首轮 ticker），它们都在微任务里 setState；
 * 用例里凡是要断言异步结果的都走 `waitFor`，因此这里不需要额外包 `act` ——
 * `render` 自身已经在 act 里，剩下的更新由 `waitFor` 的轮询接住。
 */
function mountTopbar() {
  return render(<Topbar />);
}

beforeEach(() => {
  window.localStorage.clear();
  ticker.mockImplementation(({ symbol }: { symbol: string }) =>
    Promise.resolve(tickerOf(symbol))
  );
  unreadCount.mockResolvedValue({ unread_count: 0 });
  headerSymbols.mockResolvedValue(prefOf(DEFAULT_HEADER_SYMBOLS));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("顶栏行情条", () => {
  it("默认四档都在，BNB 在其列", async () => {
    mountTopbar();

    await waitFor(() => {
      expect(screen.getByText("BNB")).toBeDefined();
    });

    for (const q of ["BTC", "ETH", "SOL", "BNB"]) {
      expect(screen.getByText(q)).toBeDefined();
    }

    // 每档都走了 ticker 接口（不是硬编码价）
    await waitFor(() => {
      expect(ticker).toHaveBeenCalledWith({ source: "binance", symbol: "BNB/USDT" });
    });
  });

  it("BNB 价格按其口径显示（三位数一位小数）", async () => {
    mountTopbar();

    await waitFor(() => {
      expect(screen.getByText("560.3")).toBeDefined();
    });
  });

  it("没点开时没有 K 线弹窗", async () => {
    mountTopbar();
    // 等首轮取数落地再断言：否则那次 setState 会落在断言之后（act 噪声）
    await waitFor(() => {
      expect(ticker).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("kline-stub")).toBeNull();
  });

  it("点 BNB 打开弹窗，且带的是 BNB/USDT 而不是别的品种", async () => {
    mountTopbar();

    const chip = await waitFor(() => {
      const el = screen.getByText("BNB").closest("button");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    // 等首轮报价落地（否则它的 setState 会落在断言之后，变成 act 噪声）
    await waitFor(() => expect(screen.getByText("560.3")).toBeDefined());
    fireEvent.click(chip);

    const stub = screen.getByTestId("kline-stub");
    expect(stub.getAttribute("data-symbol")).toBe("BNB/USDT");
  });

  it("点 ETH 打开的是 ETH/USDT（跟随所点那一条）", async () => {
    mountTopbar();

    const chip = await waitFor(() => {
      const el = screen.getByText("ETH").closest("button");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    await waitFor(() => expect(screen.getByText("3,200")).toBeDefined());
    fireEvent.click(chip);

    expect(screen.getByTestId("kline-stub").getAttribute("data-symbol")).toBe(
      "ETH/USDT"
    );
  });

  it("关掉后弹窗消失", async () => {
    mountTopbar();

    const chip = await waitFor(() => {
      const el = screen.getByText("SOL").closest("button");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    await waitFor(() => expect(screen.getByText("150.5")).toBeDefined());
    fireEvent.click(chip);
    expect(screen.getByTestId("kline-stub")).toBeDefined();

    fireEvent.click(screen.getByTestId("kline-stub-close"));
    expect(screen.queryByTestId("kline-stub")).toBeNull();
  });
});

describe("顶栏行情条跟随用户的币种偏好", () => {
  it("用户配的三档就是画出来的三档（默认那档不再出现）", async () => {
    const mine = ["BTC/USDT", "ETH/USDT", "DOGE/USDT"];
    // 缓存与后端都给同一份：这样从头到尾只该按这三档取数
    window.localStorage.setItem("tradeclaw_header_symbols", JSON.stringify(mine));
    headerSymbols.mockResolvedValue(prefOf(mine));

    mountTopbar();

    await waitFor(() => {
      expect(screen.getByText("DOGE")).toBeDefined();
    });
    expect(screen.queryByText("SOL")).toBeNull();
    expect(screen.queryByText("BNB")).toBeNull();

    // 取数也不碰这两档：多取一档是浪费，且那两档在页面上不存在
    await waitFor(() => {
      expect(ticker).toHaveBeenCalledWith({
        source: "binance",
        symbol: "DOGE/USDT",
      });
    });
    expect(new Set(symbolsAsked())).toEqual(new Set(mine));
  });

  it("小币按三位有效数字显示（DOGE 0.4123 → 0.412），不是「非 BTC/ETH 一律一位小数」的特例表", async () => {
    const mine = ["BTC/USDT", "ETH/USDT", "DOGE/USDT"];
    window.localStorage.setItem("tradeclaw_header_symbols", JSON.stringify(mine));
    headerSymbols.mockResolvedValue(prefOf(mine));

    mountTopbar();

    await waitFor(() => {
      expect(screen.getByText("0.412")).toBeDefined();
    });
  });

  it("冷启动（无缓存）先按默认四档取数，偏好到了之后换成用户那三档", async () => {
    // 首帧没有缓存 → 用默认四档（刻意的：宁可多四次请求，也不要先空一帧）。
    // 偏好回来后必须换成那三档，且此后不再碰 SOL/BNB。
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "DOGE/USDT"]));

    mountTopbar();

    await waitFor(() => {
      expect(screen.getByText("DOGE")).toBeDefined();
    });
    expect(screen.queryByText("SOL")).toBeNull();

    const askedAfterPref = symbolsAsked().slice(-3);
    expect(askedAfterPref).not.toContain("SOL/USDT");
    expect(askedAfterPref).not.toContain("BNB/USDT");
  });

  it("配置页保存后，同一页的顶栏立刻跟着变（不需要换页重挂载）", async () => {
    // 这是部署到 txy 上真机发现的缺陷：编辑器与顶栏各持一个 `useHeaderSymbols` 实例，
    // 保存只改了编辑器那一份，顶栏仍是老四档，必须导航离开再回来才生效。
    // 现在保存会广播给同页所有实例。
    headerSymbols.mockResolvedValue(prefOf(DEFAULT_HEADER_SYMBOLS));

    const { default: HeaderSymbolsEditor } = await import(
      "@/components/settings/HeaderSymbolsEditor"
    );
    render(
      <>
        <Topbar />
        <HeaderSymbolsEditor />
      </>
    );

    await waitFor(() => {
      expect(screen.getAllByText("BNB/USDT").length).toBeGreaterThan(0);
    });

    // 在编辑器里把 BNB 删掉、加进 DOGE，然后保存
    fireEvent.click(
      screen.getByRole("button", { name: "删除 BNB/USDT" })
    );
    fireEvent.change(screen.getByLabelText("添加币种"), {
      target: { value: "doge" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    saveHeaderSymbols.mockResolvedValue(
      prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"])
    );
    fireEvent.click(screen.getByRole("button", { name: "保存币种" }));

    // 顶栏**不换页**就该换成 DOGE 那四档
    await waitFor(() => {
      expect(screen.getByText("DOGE")).toBeDefined();
    });
    expect(screen.queryByText("BNB")).toBeNull();
  });

  it("偏好取不到时退回默认四档，顶栏不空掉", async () => {
    headerSymbols.mockRejectedValue(new Error("401"));

    mountTopbar();

    await waitFor(() => {
      expect(screen.getByText("BTC")).toBeDefined();
    });
    for (const q of ["BTC", "ETH", "SOL", "BNB"]) {
      expect(screen.getByText(q)).toBeDefined();
    }
  });

  it("首屏用 localStorage 缓存（少闪一帧默认值）", async () => {
    const mine = ["BTC/USDT", "ETH/USDT", "DOGE/USDT"];
    window.localStorage.setItem("tradeclaw_header_symbols", JSON.stringify(mine));
    // 后端**永远不答**：屏幕上留下的就只能是首帧那份（缓存），
    // 而不是「默认四档被后端答案替换掉」之后的结果。
    headerSymbols.mockReturnValue(new Promise(() => {}));

    mountTopbar();

    // 首轮报价落地就说明首帧那份（缓存）已经画完并取过数了
    await waitFor(() => expect(screen.getByText("0.412")).toBeDefined());
    expect(screen.getByText("DOGE")).toBeDefined();
    expect(screen.queryByText("SOL")).toBeNull();
  });

  it("缓存里是不合规的清单（只有 2 个）时不用它，退回默认四档", async () => {
    window.localStorage.setItem(
      "tradeclaw_header_symbols",
      JSON.stringify(["BTC/USDT", "ETH/USDT"])
    );
    // 后端**永远不答**，屏幕上的档位就是「缓存被判定不合规之后用了什么」：
    // 用了坏缓存 → 只有 BTC/ETH 两档；用了默认值 → 有 SOL 与 BNB。
    headerSymbols.mockReturnValue(new Promise(() => {}));

    mountTopbar();

    await waitFor(() => expect(screen.getByText("560.3")).toBeDefined());
    expect(screen.getByText("SOL")).toBeDefined();
    expect(screen.getByText("BNB")).toBeDefined();
  });
});
