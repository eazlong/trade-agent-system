/**
 * 数值与盈亏的统一格式化。
 *
 * 原先这两个函数内联在 trading/page.tsx；右侧边栏当时的做法是自己"编"收益
 * （Math.random），造成两个页面口径不一致。收益显示必须只有一个口径，
 * 因此抽到这里，由交易页与侧边栏共同引用。
 */

export function formatNumber(n: string | number, decimals = 2): string {
  const num = typeof n === "string" ? parseFloat(n) : n;
  if (isNaN(num)) return "0.00";
  return num.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 未实现盈亏的统一展示口径：正数带 +，负数带 -，0/非法值中性色。 */
export function formatPnl(value: string | number): { text: string; color: string } {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num) || num === 0) return { text: "0.00", color: "text-text3" };
  const formatted = formatNumber(Math.abs(num));
  return {
    text: `${num >= 0 ? "+" : "-"}${formatted}`,
    color: num >= 0 ? "text-green" : "text-red",
  };
}
