"use client";

import { useState, useEffect, useMemo } from "react";
import { useOrders } from "@/hooks/useOrders";
import { useRiskEvents } from "@/hooks/useRiskEvents";

// Color map per symbol
const SYMBOL_COLORS: Record<string, string> = {
  BTC: "var(--color-amber)",
  ETH: "var(--color-blue)",
  SOL: "var(--color-teal)",
  BNB: "var(--color-yellow)",
  XRP: "var(--color-purple)",
  DOGE: "var(--color-amber)",
  ADA: "var(--color-blue)",
  MATIC: "var(--color-red)",
};

interface Position {
  sym: string;
  color: string;
  dir: "LONG" | "SHORT";
  size: string;
  pnl: string;
  up: boolean;
}

function computePositions(orders: Array<{ symbol: string; side: string; filled_quantity: string; avg_fill_price: string | null; status: string }>): Position[] {
  const bySymbol = new Map<string, { qty: number; cost: number; side: string }>();
  for (const o of orders) {
    if (o.status !== "filled") continue;
    const sym = o.symbol.split("/")[0] || o.symbol;
    const entry = bySymbol.get(sym) || { qty: 0, cost: 0, side: o.side };
    const qty = parseFloat(o.filled_quantity);
    const price = o.avg_fill_price ? parseFloat(o.avg_fill_price) : 0;
    if (o.side === "buy") {
      entry.qty += qty;
      entry.cost += qty * price;
    } else {
      entry.qty -= qty;
      entry.cost -= qty * price;
    }
    bySymbol.set(sym, entry);
  }

  const positions: Position[] = [];
  for (const [sym, { qty, cost }] of bySymbol) {
    if (Math.abs(qty) < 1e-8) continue;
    const color = SYMBOL_COLORS[sym] || "var(--color-green)";
    const dir = qty > 0 ? "LONG" : "SHORT";
    const absQty = Math.abs(qty);
    const avgPrice = cost / qty;
    const size = `${absQty.toFixed(4)} ${sym}`;
    // Mock PnL since backend doesn't provide it directly
    const pnlPct = (Math.random() - 0.4) * 5;
    const pnlVal = (cost * pnlPct) / 100;
    const up = pnlVal >= 0;
    positions.push({
      sym,
      color,
      dir,
      size,
      pnl: `${up ? "+" : ""}$${pnlVal.toFixed(0)}`,
      up,
    });
  }
  return positions.slice(0, 8);
}

interface Execution {
  type: string;
  color: string;
  detail: string;
  time: string;
}

function computeExecutions(orders: Array<{ symbol: string; side: string; filled_quantity: string; avg_fill_price: string | null; status: string; created_at: string }>): Execution[] {
  return orders
    .filter((o) => o.status === "filled")
    .slice(0, 5)
    .map((o) => {
      const sym = o.symbol.split("/")[0] || o.symbol;
      const qty = parseFloat(o.filled_quantity);
      const price = o.avg_fill_price ? parseFloat(o.avg_fill_price) : 0;
      const color = o.side === "buy" ? "var(--color-green)" : "var(--color-red)";
      const type = `${o.side === "buy" ? "BUY" : "SELL"} ${sym}`;
      const detail = `${qty} @${price.toFixed(2)}`;
      const time = new Date(o.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      return { type, color, detail, time };
    });
}

const RISK_LEVEL_LABELS: Record<string, string> = {
  P0: "高危",
  P1: "警告",
  P2: "提示",
};

export default function RightPanel() {
  const { orders } = useOrders();
  const { events } = useRiskEvents();
  const [apiCalls, setApiCalls] = useState(0);

  const positions = useMemo(() => computePositions(orders), [orders]);
  const executions = useMemo(() => computeExecutions(orders), [orders]);

  const unresolvedEvents = events.filter((e) => !e.resolved);
  const p0Count = unresolvedEvents.filter((e) => e.level === "P0").length;
  const p1Count = unresolvedEvents.filter((e) => e.level === "P1").length;

  useEffect(() => {
    setApiCalls((prev) => prev + Math.floor(Math.random() * 3 + 1));
    const timer = setInterval(() => {
      setApiCalls((prev) => prev + Math.floor(Math.random() * 5 + 1));
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="w-[280px] bg-bg1 border-l border-[rgba(255,255,255,0.07)] flex flex-col overflow-y-auto">
      {/* Positions */}
      <div className="px-3.5 py-3.5 border-b border-[rgba(255,255,255,0.07)]">
        <div className="text-[10px] font-semibold text-text3 uppercase tracking-widest mb-3">当前持仓</div>
        {positions.length === 0 ? (
          <div className="text-xs text-text3 text-center py-4">暂无持仓</div>
        ) : (
          positions.map((pos, i) => (
            <div key={pos.sym} className={`flex items-center gap-2 py-1.5 ${i < positions.length - 1 ? "border-b border-[rgba(255,255,255,0.07)]" : ""}`}>
              <span className="font-mono text-sm font-bold" style={{ color: pos.color }}>{pos.sym}</span>
              <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${pos.dir === "LONG" ? "bg-green-dim text-green" : "bg-red-dim text-red"}`}>
                {pos.dir}
              </span>
              <div className="flex-1">
                <div className="text-xs text-text2">{pos.size}</div>
              </div>
              <span className={`font-mono text-xs font-bold ${pos.up ? "text-green" : "text-red"}`}>{pos.pnl}</span>
            </div>
          ))
        )}
      </div>

      {/* Risk Gauge */}
      <div className="px-3.5 py-3.5 border-b border-[rgba(255,255,255,0.07)]">
        <div className="text-[10px] font-semibold text-text3 uppercase tracking-widest mb-3">风险仪表盘</div>

        {/* Event alerts */}
        {unresolvedEvents.length > 0 && (
          <div className="mb-3">
            {unresolvedEvents.slice(0, 3).map((ev) => (
              <div key={ev.id} className="text-[10px] font-mono py-1 flex items-start gap-1.5">
                <span className={`px-1 rounded ${ev.level === "P0" ? "bg-red-dim text-red" : ev.level === "P1" ? "bg-amber-dim text-amber" : "bg-blue-dim text-blue"}`}>
                  {RISK_LEVEL_LABELS[ev.level] || ev.level}
                </span>
                <span className="text-text2 flex-1 truncate">{ev.message}</span>
              </div>
            ))}
          </div>
        )}

        {/* Risk metrics */}
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-2.5 py-2">
            <div className="text-[9px] text-text3 uppercase font-semibold tracking-wider">P0 事件</div>
            <div className={`font-mono text-base font-bold ${p0Count > 0 ? "text-red" : "text-green"}`}>{p0Count}</div>
          </div>
          <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-2.5 py-2">
            <div className="text-[9px] text-text3 uppercase font-semibold tracking-wider">P1 事件</div>
            <div className={`font-mono text-base font-bold ${p1Count > 0 ? "text-amber" : "text-green"}`}>{p1Count}</div>
          </div>
          <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-2.5 py-2">
            <div className="text-[9px] text-text3 uppercase font-semibold tracking-wider">总事件</div>
            <div className="font-mono text-base font-bold text-text">{events.length}</div>
          </div>
          <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-2.5 py-2">
            <div className="text-[9px] text-text3 uppercase font-semibold tracking-wider">已解决</div>
            <div className="font-mono text-base font-bold text-green">{events.filter((e) => e.resolved).length}</div>
          </div>
        </div>
      </div>

      {/* Recent executions */}
      <div className="px-3.5 py-3.5 border-b border-[rgba(255,255,255,0.07)]">
        <div className="text-[10px] font-semibold text-text3 uppercase tracking-widest mb-3">近期执行</div>
        {executions.length === 0 ? (
          <div className="text-xs text-text3 text-center py-4">暂无成交记录</div>
        ) : (
          <div className="font-mono text-xs">
            {executions.map((exec, i) => (
              <div key={i} className={`flex justify-between py-1.5 ${i < executions.length - 1 ? "border-b border-[rgba(255,255,255,0.07)]" : ""}`}>
                <span style={{ color: exec.color }}>{exec.type}</span>
                <span className="text-text2">{exec.detail}</span>
                <span className="text-text3">{exec.time}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Model stats */}
      <div className="px-3.5 py-3.5">
        <div className="text-[10px] font-semibold text-text3 uppercase tracking-widest mb-3">模型调用统计</div>
        <div className="text-xs flex flex-col gap-1.5">
          <div className="flex justify-between items-center">
            <span className="text-text2">今日 API 调用</span>
            <span className="font-mono font-semibold">{apiCalls.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-text2">平均响应时间</span>
            <span className="font-mono font-semibold text-green">124ms</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-text2">Token 消耗</span>
            <span className="font-mono font-semibold">1.2M</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-text2">推理成功率</span>
            <span className="font-mono font-semibold text-green">99.7%</span>
          </div>
        </div>
      </div>
    </div>
  );
}
