/**
 * 顶栏行情币种：**共享常量与价格口径**（纯函数，无 React、无请求）。
 *
 * 这份文件的理由是「同一件事只能有一个说法」：
 *
 * - **条数边界**（3–5）在后端是硬门槛（`apps/authentication/preferences.py` 的
 *   `HEADER_SYMBOLS_MIN/MAX`）。前端这份只是**提示**——按钮置灰、表单报哪句话——真正
 *   的 400 由后端出。两处数值不一致的表现是「按钮亮着、点了报错」。
 * - **默认值**（BTC/ETH/SOL/BNB）与后端 `DEFAULT_HEADER_SYMBOLS`、以及实时行情供给的
 *   订阅清单（`apps/datasource/ws_runner.py::DEFAULT_FEED_SYMBOLS`）逐字相同。不一致的
 *   表现是「没改过偏好的用户看到四档，而 WS 只订阅了其中几档」——一眼看不出错，只是
 *   一个永久的「—」。
 * - **价格口径**（`formatHeaderPrice`）此前是 `if (sym.startsWith("BTC")) ... else if
 *   (ETH) ...` 的特例表。用户能把顶栏换成 DOGE 之后，特例表就变成「认得 BTC/ETH、其余
 *   一律一位小数」——`DOGE` 的 0.1234 显示成 `0.1`。改成按**数量级**判，四档默认品种的
 *   显示结果与改之前逐字相同。
 */

/** 顶栏行情档数的版面硬约束（与后端 `HEADER_SYMBOLS_MIN/MAX` 同值）。 */
export const HEADER_SYMBOLS_MIN = 3;
export const HEADER_SYMBOLS_MAX = 5;

/** 出厂默认四档（与后端 `DEFAULT_HEADER_SYMBOLS` 同值）。 */
export const DEFAULT_HEADER_SYMBOLS: string[] = [
  "BTC/USDT",
  "ETH/USDT",
  "SOL/USDT",
  "BNB/USDT",
];

/** 计价货币。v1 只认 USDT：ticker 与 K 线两处都按 `BASE/USDT` 拼请求。 */
export const HEADER_SYMBOLS_QUOTE = "USDT";

/**
 * 顶栏那一条的价格文案。**按数量级分三档，不按币种名**。
 *
 * - `>= 1000`：取整带千分位（`68,000` / `3,200`）。四位数的个位每秒都在跳，进顶栏只是噪声。
 * - `>= 1`：一位小数（`150.5` / `12.3`）。三位数保留一位才看得出变化。
 * - `< 1`：**三位有效数字**（`0.0688`）。这一档不是精度偏好，是必需的——DOGE 实测
 *   0.06882 若按一位小数变成 `0.1`，读数差 45%，人照着这个数判断会被骗。三位有效数字
 *   把误差压到 1% 以内，宽度也只多两个字符。
 *
 * 早先的实现是「BTC/ETH 取整、其余一位小数」的特例表；用户能把顶栏换成任意品种之后，
 * 特例表就变成「只认得那四个，别的一律一位小数」——上面那 45% 的错就是这么来的。
 */
export function formatHeaderPrice(price: number): string {
  if (!Number.isFinite(price)) return "—";
  if (price >= 1000) return price.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (price >= 1) return price.toFixed(1);
  // 三位有效数字；去掉尾零（`0.0500` → `0.05`）——顶栏每个字符都是横向预算
  return price.toPrecision(3).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

/**
 * 归一一种用户写法，返回 `BASE/USDT`；不合法返回 `null`。
 *
 * **与后端 `normalize_header_symbol` 同一套规矩**（大小写不敏感、`btc` / `btcusdt` /
 * `BTC/USDT` 等价、只认 USDT 计价）。前端这份只用于即时提示与「保存前把 `btc` 变成
 * `BTC/USDT`」，不承担校验职责：绕过前端直接 PUT 照样得被后端挡住。
 */
export function normalizeHeaderSymbol(raw: string): string | null {
  const text = raw.trim().toUpperCase().replace(/ /g, "");
  if (!text) return null;

  const replaced = text.replace(/-/g, "/");
  const slash = replaced.indexOf("/");
  let base: string;
  if (slash >= 0) {
    const quote = replaced.slice(slash + 1);
    if (quote !== HEADER_SYMBOLS_QUOTE || quote.includes("/")) return null;
    base = replaced.slice(0, slash);
  } else if (
    text.endsWith(HEADER_SYMBOLS_QUOTE) &&
    text.length > HEADER_SYMBOLS_QUOTE.length
  ) {
    base = text.slice(0, -HEADER_SYMBOLS_QUOTE.length);
  } else {
    base = text;
  }

  if (!/^[A-Z0-9]{2,15}$/.test(base)) return null;
  return `${base}/${HEADER_SYMBOLS_QUOTE}`;
}

/** 一份清单的即时校验，返回**给人看的那句话**（`null` = 通过）。 */
export function validateHeaderSymbols(raw: string[]): string | null {
  const normalized: string[] = [];
  for (const item of raw) {
    if (!item.trim()) return "币种不能为空";
    const symbol = normalizeHeaderSymbol(item);
    if (symbol === null) {
      return `币种写法看不懂：${item.trim()}（示例：BTC 或 BTC/USDT，只支持 USDT 计价）`;
    }
    if (normalized.includes(symbol)) return `币种重复：${symbol}`;
    normalized.push(symbol);
  }
  if (
    normalized.length < HEADER_SYMBOLS_MIN ||
    normalized.length > HEADER_SYMBOLS_MAX
  ) {
    return `顶栏行情币种至少 ${HEADER_SYMBOLS_MIN} 个，最多 ${HEADER_SYMBOLS_MAX} 个（当前 ${normalized.length} 个）`;
  }
  return null;
}
