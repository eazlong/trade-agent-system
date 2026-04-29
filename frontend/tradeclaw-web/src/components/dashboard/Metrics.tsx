"use client";

import { useEffect, useState } from "react";
import { tradingApi, TradingSummary } from "@/lib/api";

export default function Metrics() {
  const [summary, setSummary] = useState<TradingSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    tradingApi
      .getSummary()
      .then((data) => setSummary(data))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3"
          >
            <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">
              加载失败
            </div>
            <div className="font-mono text-sm text-red">{error}</div>
          </div>
        ))}
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3 animate-pulse"
          >
            <div className="h-3 bg-bg rounded w-16 mb-2" />
            <div className="h-7 bg-bg rounded w-24" />
          </div>
        ))}
      </div>
    );
  }

  const pnl = parseFloat(summary.today_realized_pnl);
  const pnlDisplay = pnl >= 0 ? `↑ +${pnl.toFixed(2)}` : `↓ ${pnl.toFixed(2)}`;
  const pnlColor = pnl >= 0 ? "text-green" : "text-red";

  return (
    <div className="grid grid-cols-3 gap-3">
      <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3">
        <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">
          净值
        </div>
        <div className="font-mono text-2xl font-semibold text-green leading-none">
          {parseFloat(summary.total_equity).toFixed(4)}
        </div>
        <div className={`font-mono text-xs mt-1 ${pnlColor}`}>
          {pnlDisplay} 今日
        </div>
      </div>
      <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3">
        <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">
          今日盈亏
        </div>
        <div className={`font-mono text-2xl font-semibold leading-none ${pnlColor}`}>
          {pnlDisplay}
        </div>
        <div className="font-mono text-xs mt-1 text-text2">已实现</div>
      </div>
      <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3">
        <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">
          活跃订单
        </div>
        <div
          className="font-mono text-2xl font-semibold leading-none"
          style={{ color: "var(--color-blue)" }}
        >
          {summary.active_orders_count}
        </div>
        <div className="font-mono text-xs mt-1 text-text2">
          今日 {summary.total_orders_today} 单
        </div>
      </div>
    </div>
  );
}
