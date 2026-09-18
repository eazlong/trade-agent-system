"use client";

import { useMemo } from "react";
import { useOrders } from "@/hooks/useOrders";
import { usePositions } from "@/hooks/usePositions";
import { useRiskEvents } from "@/hooks/useRiskEvents";
import { formatPnl } from "@/lib/format";
import type { PositionInfo } from "@/lib/api";

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
  pnlColor: string;
}

/**
 * 持仓行**只**来自后端持仓接口 GET /api/trading/positions/（字段含
 * entry_price / mark_price / unrealized_pnl）。
 *
 * 此前这里的实现有两个错误（用户报障"收益数据不对"）：
 *   1. 收益是 `Math.random()` 编的（注释："Mock PnL since backend doesn't provide
 *      it directly"），每次刷新都变 —— 而该前提早已过期：后端一直返回
 *      unrealized_pnl，交易页正是用它正确展示"未实现盈亏"；
 *   2. 持仓本身是用本地成交流水在客户端净额拼的（忽略交易所既有持仓、
 *      跨账户聚合也不对）。
 * 现在两者都统一到接口口径，与交易页共用 formatPnl。
 */
function toPositionRows(list: PositionInfo[]): Position[] {
  return list.slice(0, 8).map((p) => {
    const sym = p.symbol.split("/")[0] || p.symbol;
    const pnl = formatPnl(p.unrealized_pnl);
    const qty = parseFloat(p.quantity);
    return {
      sym,
      color: SYMBOL_COLORS[sym] || "var(--color-green)",
      dir: p.side === "long" ? "LONG" : "SHORT",
      size: `${isNaN(qty) ? p.quantity : Math.abs(qty).toFixed(4)} ${sym}`,
      pnl: pnl.text,
      pnlColor: pnl.color,
    };
  });
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
  const { positions: positionData } = usePositions();
  const { events } = useRiskEvents();

  const positions = useMemo(
    () => toPositionRows(positionData.positions),
    [positionData.positions]
  );
  const executions = useMemo(() => computeExecutions(orders), [orders]);

  const unresolvedEvents = events.filter((e) => !e.resolved);
  const p0Count = unresolvedEvents.filter((e) => e.level === "P0").length;
  const p1Count = unresolvedEvents.filter((e) => e.level === "P1").length;

  return (
    <div className="w-[280px] bg-bg1 border-l border-[rgba(255,255,255,0.07)] flex flex-col overflow-y-auto">
      {/* Positions */}
      <div className="px-3.5 py-3.5 border-b border-[rgba(255,255,255,0.07)]">
        <div className="text-[10px] font-semibold text-text3 uppercase tracking-widest mb-3">当前持仓</div>
        {positions.length === 0 ? (
          <div className="text-xs text-text3 text-center py-4">
            {positionData.executor_running
              ? "暂无持仓"
              : positionData.message || "交易框架未启动，持仓数据暂不可用"}
          </div>
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
              <span data-testid="position-pnl" className={`font-mono text-xs font-bold ${pos.pnlColor}`}>{pos.pnl}</span>
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

      {/* Model stats — 后端暂无真实统计来源（无 usage/统计接口；AgentAuditLog.token_used
          字段存在但全表 0 行，供不了数）。这里一律显示占位符：宁可标"未接入"，
          也不用编造的数字冒充真实数据（原实现是 Math.random + 硬编码 124ms/1.2M/99.7%）。 */}
      <div className="px-3.5 py-3.5">
        <div className="flex items-center justify-between mb-3">
          <span className="text-[10px] font-semibold text-text3 uppercase tracking-widest">模型调用统计</span>
          <span className="text-[9px] text-text3 border border-[rgba(255,255,255,0.07)] rounded px-1">未接入</span>
        </div>
        <div className="text-xs flex flex-col gap-1.5">
          {["今日 API 调用", "平均响应时间", "Token 消耗", "推理成功率"].map((label) => (
            <div key={label} className="flex justify-between items-center">
              <span className="text-text2">{label}</span>
              <span data-testid="model-stat-value" className="font-mono font-semibold text-text3">—</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
