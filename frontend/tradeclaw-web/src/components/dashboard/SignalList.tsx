"use client";

import { useState, useEffect } from "react";
import { signalMonitorApi, SignalMonitor } from "@/lib/api";

function formatTime(isoStr: string | null): string {
  if (!isoStr) return "--:--";
  const d = new Date(isoStr);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function getSignalType(signal: SignalMonitor): "buy" | "sell" | "watch" {
  if (signal.status === "triggered") return "buy";
  if (signal.status === "disabled" || signal.status === "expired") return "watch";
  const condStr = JSON.stringify(signal.condition).toLowerCase();
  if (condStr.includes("sell") || condStr.includes("short")) return "sell";
  if (condStr.includes("buy") || condStr.includes("long") || condStr.includes("cross_over")) return "buy";
  return "watch";
}

function getBorderColor(type: string): string {
  if (type === "buy") return "!border-l-green";
  if (type === "sell") return "!border-l-red";
  return "!border-l-amber";
}

function getBadgeClasses(type: string): string {
  if (type === "buy") return "bg-green-dim text-green";
  if (type === "sell") return "bg-red-dim text-red";
  return "bg-amber-dim text-amber";
}

function getSymbolColor(type: string): string {
  if (type === "buy") return "var(--color-green)";
  if (type === "sell") return "var(--color-red)";
  return "var(--color-amber)";
}

function typeLabel(type: string): string {
  if (type === "buy") return "BUY";
  if (type === "sell") return "SELL";
  return "WATCH";
}

export default function SignalList() {
  const [signals, setSignals] = useState<SignalMonitor[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    signalMonitorApi
      .list()
      .then((res) => setSignals(res.data.filter((s) => s.status === "active" || s.status === "triggered")))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return <div className="p-4 text-red text-xs">加载失败: {error}</div>;
  }

  if (signals.length === 0) {
    return (
      <div className="flex items-center justify-center py-6 text-text3 text-xs">
        暂无活跃信号
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-px py-1">
      {signals.map((sig, i) => {
        const type = getSignalType(sig);
        const desc = `${sig.indicator_type} · ${sig.name}`;
        const time = formatTime(sig.last_triggered_at);
        const triggerCount = sig.trigger_count;

        return (
          <div
            key={sig.id}
            className={`flex items-center gap-2.5 px-3 py-1.5 text-xs rounded-md transition-colors cursor-default hover:bg-bg2 border-l-[2px] border-transparent ${getBorderColor(type)}`}
          >
            <span
              className={`font-mono text-[10px] font-bold px-1.5 py-0.5 rounded min-w-[36px] text-center ${getBadgeClasses(type)}`}
            >
              {typeLabel(type)}
            </span>
            <span
              className="font-mono text-xs font-semibold min-w-[60px]"
              style={{ color: getSymbolColor(type) }}
            >
              {sig.symbol}
            </span>
            <span className="flex-1 text-text2">{desc}</span>
            <span className="font-mono text-xs text-text3" title={`触发 ${triggerCount} 次`}>
              #{triggerCount}
            </span>
            <span className="font-mono text-xs text-text3 min-w-[45px] text-right">
              {time}
            </span>
          </div>
        );
      })}
    </div>
  );
}
