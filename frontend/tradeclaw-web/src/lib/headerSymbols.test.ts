/**
 * `lib/headerSymbols.ts` 的三件事：**价格口径、当归一、清单校验**。
 *
 * 这一层是纯函数，所以它值得被单独钉住——它同时被三处消费（顶栏、配置页编辑器、
 * hook 的保存前校验），任何一处口径变化都会在这里先红。
 *
 * 三条布置的理由：
 *
 * 1. **价格口径改成按数量级判，不再按币种名硬编码**。以前是
 *    `if (sym.startsWith("BTC")) ... else if (ETH) ... else toFixed(1)`——用户能把顶栏换成
 *    DOGE 之后，小币就被压成 `0.1`。这里既钉「四档默认品种的结果与改之前逐字相同」
 *    （回归），也钉小币的档位。
 * 2. **归一是与后端 `normalize_header_symbol` 的镜像**。两边不一致的表现极隐蔽：
 *    前端存了 `btc`，后端存了 `BTC/USDT`，页面上一会儿显示 `btc` 一会儿 `BTC/USDT`，
 *    而顶栏取数用的是另一个字符串 → 那一档永远「—」。
 * 3. **条数边界**（3–5）在这里是即时提示，后端才是硬门槛。但两处数值必须同值，
 *    否则会出现「按钮亮着、点了报错」。
 */

import { describe, expect, it } from "vitest";
import {
  DEFAULT_HEADER_SYMBOLS,
  HEADER_SYMBOLS_MAX,
  HEADER_SYMBOLS_MIN,
  formatHeaderPrice,
  normalizeHeaderSymbol,
  validateHeaderSymbols,
} from "@/lib/headerSymbols";

describe("formatHeaderPrice", () => {
  it("四档默认品种的显示与改动前逐字相同（回归）", () => {
    // 改动前的口径：BTC/ETH 取整带千分位，其余一位小数
    expect(formatHeaderPrice(68000)).toBe("68,000");
    expect(formatHeaderPrice(3200)).toBe("3,200");
    expect(formatHeaderPrice(3200.75)).toBe("3,201");
    expect(formatHeaderPrice(150.5)).toBe("150.5");
    expect(formatHeaderPrice(560.25)).toBe("560.3");
  });

  it("小币按三位有效数字显示，不再被压成一位小数（DOGE 实测 0.06882 曾被压成 0.1）", () => {
    // 45% 的读数误差 —— 这条是这个分档真正要防的东西，不是精度洁癖
    expect(formatHeaderPrice(0.06882)).toBe("0.0688");
    expect(formatHeaderPrice(0.4123)).toBe("0.412");
    expect(formatHeaderPrice(0.05)).toBe("0.05");
    expect(formatHeaderPrice(0.005)).toBe("0.005");
  });

  it("1 到 1000 之间一位小数，向上取整不越档", () => {
    expect(formatHeaderPrice(12.345)).toBe("12.3");
    expect(formatHeaderPrice(999.94)).toBe("999.9");
    expect(formatHeaderPrice(1)).toBe("1.0");
  });

  it("1000 是分界：以下一位小数，以上取整带千分位", () => {
    expect(formatHeaderPrice(999.96)).toBe("1000.0");
    expect(formatHeaderPrice(1000)).toBe("1,000");
    expect(formatHeaderPrice(1234567)).toBe("1,234,567");
  });

  it("非有限值出「—」（不是 NaN 字符串）", () => {
    expect(formatHeaderPrice(Number.NaN)).toBe("—");
    expect(formatHeaderPrice(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("normalizeHeaderSymbol", () => {
  it.each([
    ["BTC", "BTC/USDT"],
    ["btc", "BTC/USDT"],
    ["btcusdt", "BTC/USDT"],
    ["BTCUSDT", "BTC/USDT"],
    ["BTC/USDT", "BTC/USDT"],
    [" btc/usdt ", "BTC/USDT"],
    ["btc-usdt", "BTC/USDT"],
    ["1000shib", "1000SHIB/USDT"],
    ["1inch", "1INCH/USDT"],
  ])("%s → %s（与后端同一套规矩）", (raw, expected) => {
    expect(normalizeHeaderSymbol(raw)).toBe(expected);
  });

  it.each(["", "   ", "B", "THISISWAYTOOLONGNAME", "BTC/USDC", "BTC/EUR", "BTX/文"])(
    "%s 归不出来（返回 null，不是抛异常）",
    (raw) => {
      expect(normalizeHeaderSymbol(raw)).toBeNull();
    },
  );
});

describe("validateHeaderSymbols", () => {
  it("默认四档通过", () => {
    expect(validateHeaderSymbols(DEFAULT_HEADER_SYMBOLS)).toBeNull();
  });

  it("恰好 3 个通过（下边界）", () => {
    expect(validateHeaderSymbols(["BTC", "ETH", "SOL"])).toBeNull();
  });

  it("恰好 5 个通过（上边界）", () => {
    expect(
      validateHeaderSymbols(["BTC", "ETH", "SOL", "BNB", "XRP"]),
    ).toBeNull();
  });

  it("2 个、6 个都不通过，且消息里带上下限（给人看的原文）", () => {
    const tooFew = validateHeaderSymbols(["BTC", "ETH"]);
    expect(tooFew).toContain(String(HEADER_SYMBOLS_MIN));
    expect(tooFew).toContain(String(HEADER_SYMBOLS_MAX));

    const tooMany = validateHeaderSymbols([
      "BTC",
      "ETH",
      "SOL",
      "BNB",
      "XRP",
      "DOGE",
    ]);
    expect(tooMany).toContain(String(HEADER_SYMBOLS_MAX));
  });

  it("重复（归一后）不通过", () => {
    const problem = validateHeaderSymbols(["BTC", "btcusdt", "ETH"]);
    expect(problem).toContain("重复");
    expect(problem).toContain("BTC/USDT");
  });

  it("空串不通过", () => {
    expect(validateHeaderSymbols(["BTC", "ETH", ""])).toContain("为空");
  });

  it("写法读不懂时报的是那一项本身（用户看得出来要改哪个）", () => {
    const problem = validateHeaderSymbols(["BTC", "ETH", "BTC/USDC"]);
    expect(problem).toContain("BTC/USDC");
  });
});
