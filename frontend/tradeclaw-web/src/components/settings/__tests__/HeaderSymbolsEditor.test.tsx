/**
 * 「配置 → 通用 → 顶栏行情币种」编辑器的界面契约。
 *
 * 这一块的风险全在**按钮状态与真实约束不一致**上，而不是「能不能保存」：
 *
 *   1. **3–5 个**：删到 3 个时「删除」必须置灰、到 5 个时「添加」与输入框必须置灰。
 *      只在提交时校验是不够的——用户会点很多次才看见那句话，而每次点都像失败了。
 *   2. **顺序可改**：上移/下移真的改变顺序（顶栏按这个顺序画）。第一项的上移、最后一项
 *      的下移必须置灰。
 *   3. **归一**：输入 `btc` 加进来要变成 `BTC/USDT`。否则页面里会出现一份后端不认的写法，
 *      而保存那一步的 400 会看起来毫无来由。
 *   4. **不做乐观更新**：保存失败时列表仍是上次真正落库的那份，红字显示后端原文。
 *      这条最要命——乐观更新会让用户以为顶栏已经换成新币种。
 *   5. **「恢复默认」的置灰读后端 `is_default`**，不靠「与默认数组比对」推。
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const headerSymbols = vi.hoisted(() => vi.fn());
const saveHeaderSymbols = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  preferencesApi: { headerSymbols, saveHeaderSymbols },
}));

import HeaderSymbolsEditor from "@/components/settings/HeaderSymbolsEditor";
import { DEFAULT_HEADER_SYMBOLS } from "@/lib/headerSymbols";

function prefOf(symbols: string[], isDefault = false) {
  return {
    header_symbols: symbols,
    is_default: isDefault,
    min: 3,
    max: 5,
    default: DEFAULT_HEADER_SYMBOLS,
  };
}

function deleteButton(symbol: string) {
  return screen.getByRole("button", { name: `删除 ${symbol}` });
}

beforeEach(() => {
  window.localStorage.clear();
  headerSymbols.mockResolvedValue(prefOf(DEFAULT_HEADER_SYMBOLS, true));
  saveHeaderSymbols.mockImplementation((symbols: string[]) =>
    Promise.resolve(prefOf(symbols, false))
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("顶栏行情币种编辑器", () => {
  it("首屏展示后端那份清单，并显示条数范围", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "DOGE/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("DOGE/USDT")).toBeDefined();
    });
    expect(screen.getByText("BTC/USDT")).toBeDefined();
    // 范围文案来自共享常量（3–5）
    expect(screen.getByText(/3–5/)).toBeDefined();
    expect(screen.queryByText("SOL/USDT")).toBeNull();
  });

  it("默认三档时删除按钮可用，删到 3 个后置灰（下边界）", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("SOL/USDT")).toBeDefined();
    });

    // 3 个时每一个删除都置灰：再删就低于下限
    for (const s of ["BTC/USDT", "ETH/USDT", "SOL/USDT"]) {
      expect(deleteButton(s)).toBeDisabled();
    }
  });

  it("4 个时删除可用；删掉一个后变成 3 个，删除全部置灰", async () => {
    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("BNB/USDT")).toBeDefined();
    });
    expect(deleteButton("BNB/USDT")).not.toBeDisabled();

    fireEvent.click(deleteButton("BNB/USDT"));

    await waitFor(() => {
      expect(screen.queryByText("BNB/USDT")).toBeNull();
    });
    expect(deleteButton("BTC/USDT")).toBeDisabled();
  });

  it("5 个时「添加」与输入框置灰（上边界）", async () => {
    headerSymbols.mockResolvedValue(
      prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"])
    );

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("XRP/USDT")).toBeDefined();
    });

    const input = screen.getByLabelText("添加币种");
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "添加" })).toBeDisabled();
    expect(screen.getByText(/已到上限 5 个/)).toBeDefined();
  });

  it("输入 btc 点「添加」→ 归一成 BTC/USDT，且不产生重复", async () => {
    headerSymbols.mockResolvedValue(prefOf(["ETH/USDT", "SOL/USDT", "BNB/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("ETH/USDT")).toBeDefined();
    });

    const input = screen.getByLabelText("添加币种");
    fireEvent.change(input, { target: { value: "btc" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => {
      expect(screen.getByText("BTC/USDT")).toBeDefined();
    });

    // 再输一次同一个（大小写不同）→ 不新增
    fireEvent.change(input, { target: { value: "BTCUSDT" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => {
      expect(screen.getAllByText("BTC/USDT")).toHaveLength(1);
    });
  });

  it("写法读不懂时「添加」置灰，并显示带示例的提示", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("SOL/USDT")).toBeDefined();
    });

    fireEvent.change(screen.getByLabelText("添加币种"), {
      target: { value: "BTC/USDC" },
    });

    expect(screen.getByRole("button", { name: "添加" })).toBeDisabled();
    expect(screen.getByText(/写法看不懂/)).toBeDefined();
  });

  it("上移/下移真的换顺序，首项上移与末项下移置灰", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("SOL/USDT")).toBeDefined();
    });

    expect(screen.getByRole("button", { name: "BTC/USDT 上移" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "SOL/USDT 下移" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "ETH/USDT 上移" }));

    // 保存时按当前顺序发出去
    fireEvent.click(screen.getByRole("button", { name: "保存币种" }));
    await waitFor(() => {
      expect(saveHeaderSymbols).toHaveBeenCalledWith([
        "ETH/USDT",
        "BTC/USDT",
        "SOL/USDT",
      ]);
    });
  });

  it("保存成功：落库并显示「已保存」", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("SOL/USDT")).toBeDefined();
    });

    fireEvent.change(screen.getByLabelText("添加币种"), {
      target: { value: "DOGE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    fireEvent.click(screen.getByRole("button", { name: "保存币种" }));

    await waitFor(() => {
      expect(saveHeaderSymbols).toHaveBeenCalledWith([
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "DOGE/USDT",
      ]);
    });
    await waitFor(() => expect(screen.getByText("已保存")).toBeDefined());
  });

  it("保存失败：显示后端原文，落库那份（顶栏的真相）不被改动，草稿留着让用户改", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "SOL/USDT"]));
    saveHeaderSymbols.mockRejectedValue(
      new Error("顶栏行情币种至少 3 个，最多 5 个（当前 2 个）")
    );

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("SOL/USDT")).toBeDefined();
    });

    fireEvent.change(screen.getByLabelText("添加币种"), {
      target: { value: "DOGE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    fireEvent.click(screen.getByRole("button", { name: "保存币种" }));

    // 后端说的那句话原样显示（不按状态码自己翻译）
    await waitFor(() => {
      expect(
        screen.getByText("顶栏行情币种至少 3 个，最多 5 个（当前 2 个）"),
      ).toBeDefined();
    });

    // **不做乐观更新**：真相（顶栏读的那份，来自后端/缓存）没被写进 DOGE，
    // 而草稿保留 DOGE 供用户改完再存一次——丢掉他刚输入的内容同样是一种暴力。
    expect(screen.getByText("DOGE/USDT")).toBeDefined();
    // 缓存（顶栏首屏读的那份）仍是并发的三档，没被这次失败塞进 DOGE
    expect(window.localStorage.getItem("tradeclaw_header_symbols")).toBe(
      JSON.stringify(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    );
    // 「已保存」不该出现（关键是这一条：失败不能被当成成功）
    expect(screen.queryByText("已保存")).toBeNull();
  });

  it("「恢复默认」在已是默认时置灰，改过之后可用并直发默认四档", async () => {
    headerSymbols.mockResolvedValue(prefOf(["BTC/USDT", "ETH/USDT", "DOGE/USDT"]));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("DOGE/USDT")).toBeDefined();
    });

    const resetBtn = screen.getByRole("button", { name: "恢复默认" });
    expect(resetBtn).not.toBeDisabled();

    fireEvent.click(resetBtn);

    await waitFor(() => {
      expect(saveHeaderSymbols).toHaveBeenCalledWith(DEFAULT_HEADER_SYMBOLS);
    });
  });

  it("手上有一份脏草稿时点「恢复默认」，编辑器也回到默认四档（不只是顶栏）", async () => {
    // txy 真机发现的缺陷：落库的清单与上一次已保存完全相同（都是默认四档）时，
    // `key={symbols.join(",")}` 没变 → 表单不重挂载 → 草稿继续显示自己改出来的五档。
    // 顶栏变了、编辑器没变，用户会以为自己那五档还在。
    headerSymbols.mockResolvedValue(prefOf(DEFAULT_HEADER_SYMBOLS, true));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("BNB/USDT")).toBeDefined();
    });

    // 造一份脏草稿：删 BNB、加 DOGE 与 XRP（不保存）
    fireEvent.click(screen.getByRole("button", { name: "删除 BNB/USDT" }));
    for (const sym of ["doge", "xrp"]) {
      fireEvent.change(screen.getByLabelText("添加币种"), {
        target: { value: sym },
      });
      fireEvent.click(screen.getByRole("button", { name: "添加" }));
    }
    expect(screen.getByText("XRP/USDT")).toBeDefined();

    // 点恢复默认：后端仍回默认四档（与上一次已保存内容相同）
    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }));

    await waitFor(() => {
      expect(saveHeaderSymbols).toHaveBeenCalledWith(DEFAULT_HEADER_SYMBOLS);
    });
    // 编辑器必须回到默认四档：DOGE / XRP 退场、BNB 回来
    await waitFor(() => {
      expect(screen.queryByText("XRP/USDT")).toBeNull();
    });
    expect(screen.queryByText("DOGE/USDT")).toBeNull();
    expect(screen.getByText("BNB/USDT")).toBeDefined();
  });

  it("已是默认时「恢复默认」置灰", async () => {
    headerSymbols.mockResolvedValue(prefOf(DEFAULT_HEADER_SYMBOLS, true));

    render(<HeaderSymbolsEditor />);

    await waitFor(() => {
      expect(screen.getByText("BNB/USDT")).toBeDefined();
    });
    expect(screen.getByRole("button", { name: "恢复默认" })).toBeDisabled();
  });
});
