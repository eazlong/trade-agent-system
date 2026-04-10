"use client";

interface SignalData {
  type: "buy" | "sell" | "watch";
  symbol: string;
  desc: string;
  conf: number;
  time: string;
  symColor: string;
}

const INITIAL_SIGNALS: SignalData[] = [
  { type: "buy", symbol: "BTC/USDT", desc: "突破关键阻力 68,000 · RSI 背离确认", conf: 91, time: "09:42", symColor: "var(--color-green)" },
  { type: "buy", symbol: "SOL/USDT", desc: "均线多头排列 · 量能放大 2.3x", conf: 78, time: "09:38", symColor: "var(--color-green)" },
  { type: "sell", symbol: "ETH/USDT", desc: "触及目标价 3,450 · 止盈条件满足", conf: 85, time: "09:35", symColor: "var(--color-red)" },
  { type: "watch", symbol: "BNB/USDT", desc: "情绪急剧下滑 · 关注风险敞口", conf: 62, time: "09:31", symColor: "var(--color-amber)" },
  { type: "buy", symbol: "AVAX/USDT", desc: "链上活跃度突增 · 机构流入信号", conf: 74, time: "09:28", symColor: "var(--color-green)" },
  { type: "sell", symbol: "MATIC/USDT", desc: "跌破支撑 · 空头主导 · 回避风险", conf: 69, time: "09:22", symColor: "var(--color-red)" },
  { type: "watch", symbol: "ARB/USDT", desc: "等待成交量确认 · 突破未验证", conf: 55, time: "09:15", symColor: "var(--color-amber)" },
];

export default function SignalList() {
  const typeLabel = (type: string) => {
    if (type === "buy") return "BUY";
    if (type === "sell") return "SELL";
    return "⚠";
  };

  return (
    <div className="flex flex-col gap-px py-1">
      {INITIAL_SIGNALS.map((sig, i) => (
        <div
          key={i}
          className={`flex items-center gap-2.5 px-3 py-1.5 text-xs rounded-md transition-colors cursor-default hover:bg-bg2 border-l-[2px] border-transparent ${
            sig.type === "buy" ? "!border-l-green" : sig.type === "sell" ? "!border-l-red" : "!border-l-amber"
          }`}
        >
          <span
            className={`font-mono text-[10px] font-bold px-1.5 py-0.5 rounded min-w-[36px] text-center ${
              sig.type === "buy"
                ? "bg-green-dim text-green"
                : sig.type === "sell"
                ? "bg-red-dim text-red"
                : "bg-amber-dim text-amber"
            }`}
          >
            {typeLabel(sig.type)}
          </span>
          <span className="font-mono text-xs font-semibold min-w-[60px]" style={{ color: sig.symColor }}>
            {sig.symbol}
          </span>
          <span className="flex-1 text-text2">{sig.desc}</span>
          <span className={`font-mono text-xs font-semibold ${sig.conf >= 80 ? "text-green" : sig.conf >= 60 ? "text-amber" : "text-red"}`}>
            {sig.conf}%
          </span>
          <span className="font-mono text-xs text-text3 min-w-[45px] text-right">{sig.time}</span>
        </div>
      ))}
    </div>
  );
}
