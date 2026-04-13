"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import { useOrders } from "@/hooks/useOrders";
import { usePositions } from "@/hooks/usePositions";
import { useAccounts } from "@/hooks/useAccounts";
import type { Order, ExchangeAccountWithBalance } from "@/lib/api";
import { useState } from "react";

function formatNumber(n: string | number, decimals = 2): string {
  const num = typeof n === "string" ? parseFloat(n) : n;
  if (isNaN(num)) return "0.00";
  return num.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatPnl(value: string | number): { text: string; color: string } {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num) || num === 0) return { text: "0.00", color: "text-text3" };
  const formatted = formatNumber(Math.abs(num));
  return {
    text: `${num >= 0 ? "+" : "-"}${formatted}`,
    color: num >= 0 ? "text-green" : "text-red",
  };
}

function statusLabel(status: Order["status"]): string {
  const map: Record<Order["status"], string> = {
    pending: "待提交",
    submitted: "已提交",
    partial: "部分成交",
    filled: "已成交",
    cancelled: "已取消",
    failed: "失败",
  };
  return map[status] || status;
}

function statusBadge(status: Order["status"]): string {
  const active = ["pending", "submitted", "partial"].includes(status);
  return active
    ? "bg-green-dim text-green"
    : status === "failed"
      ? "bg-red-dim text-red"
      : "bg-bg2 text-text3";
}

// ── Single Account Card ──

function AccountMiniCard({
  account,
  connected,
}: {
  account: ExchangeAccountWithBalance;
  connected: boolean;
}) {
  const bal = account.balance;
  const total = bal?.total ?? "0";
  const available = bal?.available ?? "—";
  const used = bal?.used ?? "—";

  const indicatorColor = account.testnet
    ? "var(--color-blue)"
    : "var(--color-green)";
  const badgeText = account.testnet ? "模拟" : "实盘";
  const badgeColor = account.testnet
    ? "bg-blue-dim text-blue"
    : "bg-green-dim text-green";

  return (
    <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
      <div className="px-3 py-2.5 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ background: indicatorColor, boxShadow: `0 0 6px ${indicatorColor}` }}
          />
          <span className="text-xs font-semibold text-text capitalize">{account.exchange}</span>
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${badgeColor}`}>
            {badgeText}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {connected ? (
            <div className="w-1.5 h-1.5 rounded-full bg-green animate-pulse-slow" />
          ) : (
            <div className="w-1.5 h-1.5 rounded-full bg-text3" />
          )}
        </div>
      </div>
      <div className="p-3">
        {account.label && (
          <div className="text-[10px] text-text3 mb-2 truncate">{account.label}</div>
        )}
        <div className="grid grid-cols-2 gap-y-2 gap-x-3">
          <div>
            <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold">总资产</div>
            <div className="font-mono text-sm font-bold text-text mt-0.5">${formatNumber(total)}</div>
          </div>
          <div>
            <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold">可用余额</div>
            <div className="font-mono text-[11px] font-semibold text-text2 mt-0.5">
              ${typeof available === "string" ? formatNumber(available) : "—"}
            </div>
          </div>
          <div>
            <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold">已用保证金</div>
            <div className="font-mono text-[11px] font-semibold text-text2 mt-0.5">
              ${typeof used === "string" ? formatNumber(used) : "—"}
            </div>
          </div>
          <div>
            <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold">状态</div>
            <div className={`text-[11px] font-semibold mt-0.5 ${connected ? "text-green" : "text-text3"}`}>
              {connected ? "已连接" : "未连接"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Positions Section for a single account ──

function AccountPositions({
  account,
  positions,
}: {
  account: ExchangeAccountWithBalance;
  positions: { symbol: string; side: string; quantity: string; entry_price: string; mark_price: string; unrealized_pnl: string }[];
}) {
  if (positions.length === 0) return null;

  return (
    <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
        <div className="text-xs font-semibold flex items-center gap-2">
          <svg width="12" height="12" viewBox="0 0 12 12">
            <rect x="1" y="3" width="10" height="6" rx="1" fill="none" stroke="var(--color-teal)" strokeWidth="1.2" />
            <line x1="4" y1="3" x2="4" y2="9" stroke="var(--color-teal)" strokeWidth="1" />
            <line x1="8" y1="3" x2="8" y2="9" stroke="var(--color-teal)" strokeWidth="1" />
          </svg>
          {account.exchange} · {account.label || (account.testnet ? "模拟" : "实盘")}
          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-teal-dim text-teal">
            {positions.length} 个持仓
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[rgba(255,255,255,0.05)]">
              <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">品种</th>
              <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">方向</th>
              <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">数量</th>
              <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">开仓价</th>
              <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">标记价</th>
              <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">未实现盈亏</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos, i) => {
              const pnl = formatPnl(pos.unrealized_pnl);
              return (
                <tr key={`${pos.symbol}-${i}`} className="border-b border-[rgba(255,255,255,0.03)] hover:bg-bg2/50">
                  <td className="py-2.5 px-4 font-mono font-semibold text-text">{pos.symbol}</td>
                  <td className="py-2.5 px-4">
                    <span
                      className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                        pos.side === "long" ? "bg-green-dim text-green" : "bg-red-dim text-red"
                      }`}
                    >
                      {pos.side === "long" ? "多" : "空"}
                    </span>
                  </td>
                  <td className="py-2.5 px-4 font-mono text-right text-text2">{formatNumber(pos.quantity, 4)}</td>
                  <td className="py-2.5 px-4 font-mono text-right text-text2">{formatNumber(pos.entry_price)}</td>
                  <td className="py-2.5 px-4 font-mono text-right text-text2">{formatNumber(pos.mark_price)}</td>
                  <td className={`py-2.5 px-4 font-mono text-right font-semibold ${pnl.color}`}>{pnl.text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Orders Section for a single account ──

function AccountOrders({
  account,
  orders,
}: {
  account: ExchangeAccountWithBalance;
  orders: Order[];
}) {
  if (orders.length === 0) return null;

  return (
    <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
        <div className="text-xs font-semibold flex items-center gap-2">
          <svg width="12" height="12" viewBox="0 0 12 12">
            <circle cx="6" cy="6" r="5" fill="none" stroke="var(--color-amber)" strokeWidth="1.2" />
            <line x1="6" y1="3" x2="6" y2="6.5" stroke="var(--color-amber)" strokeWidth="1.2" strokeLinecap="round" />
            <line x1="6" y1="6.5" x2="8.5" y2="8" stroke="var(--color-amber)" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
          {account.exchange} · {account.label || (account.testnet ? "模拟" : "实盘")}
          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-amber-dim text-amber">
            {orders.length} 个活跃订单
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[rgba(255,255,255,0.05)]">
              <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">时间</th>
              <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">品种</th>
              <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">方向</th>
              <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">类型</th>
              <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">价格</th>
              <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">已成交</th>
              <th className="text-center py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">状态</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const sideLabel = order.side === "buy" ? "买" : "卖";
              const sideColor = order.side === "buy" ? "text-green" : "text-red";
              const typeLabel = order.order_type === "market" ? "市价" : "限价";
              return (
                <tr key={order.id} className="border-b border-[rgba(255,255,255,0.03)] hover:bg-bg2/50">
                  <td className="py-2.5 px-4 font-mono text-text3">
                    {new Date(order.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </td>
                  <td className="py-2.5 px-4 font-mono font-semibold text-text">{order.symbol}</td>
                  <td className={`py-2.5 px-4 font-semibold ${sideColor}`}>{sideLabel}</td>
                  <td className="py-2.5 px-4 text-text2">{typeLabel}</td>
                  <td className="py-2.5 px-4 font-mono text-right text-text2">
                    {order.price ? formatNumber(order.price) : "—"}
                  </td>
                  <td className="py-2.5 px-4 font-mono text-right text-text2">
                    {formatNumber(order.filled_quantity, 4)} / {formatNumber(order.quantity, 4)}
                  </td>
                  <td className="py-2.5 px-4 text-center">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${statusBadge(order.status)}`}>
                      {statusLabel(order.status)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main Page ──

export default function TradingPage() {
  const { accounts, loading: accLoading } = useAccounts();
  const { positions, loading: posLoading } = usePositions();
  const { orders, loading: ordersLoading } = useOrders();

  const [activeSection, setActiveSection] = useState<"all" | "live" | "paper">("all");

  const isLoading = accLoading || posLoading || ordersLoading;
  const hasExecutor = positions.executor_running;

  // Group accounts by testnet
  const liveAccounts = accounts.filter((a) => !a.testnet);
  const paperAccounts = accounts.filter((a) => a.testnet);

  // Group positions by exchange_account_id (using exchange name as fallback)
  const positionsByAccount = new Map<string, typeof positions.positions>();
  for (const pos of positions.positions) {
    const key = pos.exchange_account_id || pos.exchange;
    if (!positionsByAccount.has(key)) positionsByAccount.set(key, []);
    positionsByAccount.get(key)!.push(pos);
  }

  // Group active orders by exchange_account
  const activeOrders = orders.filter((o) =>
    ["pending", "submitted", "partial"].includes(o.status)
  );
  const ordersByAccount = new Map<string, Order[]>();
  for (const order of activeOrders) {
    const key = String(order.exchange_account);
    if (!ordersByAccount.has(key)) ordersByAccount.set(key, []);
    ordersByAccount.get(key)!.push(order);
  }

  // Determine visible accounts
  const visibleAccounts =
    activeSection === "live"
      ? liveAccounts
      : activeSection === "paper"
        ? paperAccounts
        : [...liveAccounts, ...paperAccounts];

  const totalPositions = positions.positions.length;
  const totalActiveOrders = activeOrders.length;

  return (
    <DashboardShell>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-bold">交易中心</h1>
          {!isLoading && (
            <span className="text-[10px] text-text3">
              {accounts.length} 个账户 · {totalPositions} 个持仓 · {totalActiveOrders} 活跃订单
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {isLoading && (
            <div className="text-[10px] text-text3 animate-pulse">加载数据中...</div>
          )}
          {/* Section filter */}
          <div className="flex gap-1">
            {(["all", "live", "paper"] as const).map((key) => {
              const labels = { all: "全部", live: "实盘", paper: "模拟" };
              return (
                <button
                  key={key}
                  onClick={() => setActiveSection(key)}
                  className={`px-2.5 py-1 rounded-md text-[10px] font-semibold cursor-pointer transition-all border ${
                    activeSection === key
                      ? "bg-green-dim border-green/20 text-green"
                      : "bg-bg2 border-[rgba(255,255,255,0.07)] text-text3 hover:text-text"
                  }`}
                >
                  {labels[key]}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Executor status warning */}
      {!hasExecutor && !posLoading && accounts.length === 0 && (
        <div className="bg-amber-dim/20 border border-amber/30 rounded-lg px-4 py-3 flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-amber" />
          <span className="text-xs text-amber">
            交易框架未启动，持仓和余额数据暂不可用。请先启动交易框架。
          </span>
        </div>
      )}

      {/* Account Cards Grid */}
      {visibleAccounts.length > 0 ? (
        <div className="grid grid-cols-2 gap-3">
          {visibleAccounts.map((acc) => (
            <AccountMiniCard key={acc.id} account={acc} connected={hasExecutor} />
          ))}
        </div>
      ) : (
        !isLoading && (
          <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl px-4 py-8 text-center">
            <div className="text-xs text-text3">
              {activeSection === "all"
                ? "暂无交易所账户，请前往 配置 → 交易所 添加"
                : activeSection === "live"
                  ? "暂无实盘账户"
                  : "暂无模拟账户"}
            </div>
          </div>
        )
      )}

      {/* Positions & Orders per account */}
      {visibleAccounts.map((acc) => {
        const accPositions = positionsByAccount.get(acc.id) || positionsByAccount.get(acc.exchange) || [];
        const accOrders = ordersByAccount.get(acc.id) || [];
        return (
          <div key={acc.id} className="flex flex-col gap-3">
            <AccountPositions account={acc} positions={accPositions} />
            <AccountOrders account={acc} orders={accOrders} />
          </div>
        );
      })}

      {/* Fallback: show all positions/orders if no accounts yet */}
      {accounts.length === 0 && hasExecutor && totalPositions > 0 && (
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
            <div className="text-xs font-semibold flex items-center gap-2">
              <svg width="12" height="12" viewBox="0 0 12 12">
                <rect x="1" y="3" width="10" height="6" rx="1" fill="none" stroke="var(--color-teal)" strokeWidth="1.2" />
                <line x1="4" y1="3" x2="4" y2="9" stroke="var(--color-teal)" strokeWidth="1" />
                <line x1="8" y1="3" x2="8" y2="9" stroke="var(--color-teal)" strokeWidth="1" />
              </svg>
              当前持仓
            </div>
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-teal-dim text-teal">
              {totalPositions} 个
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[rgba(255,255,255,0.05)]">
                  <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">交易所</th>
                  <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">品种</th>
                  <th className="text-left py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">方向</th>
                  <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">数量</th>
                  <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">开仓价</th>
                  <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">标记价</th>
                  <th className="text-right py-2.5 px-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">未实现盈亏</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.map((pos, i) => {
                  const pnl = formatPnl(pos.unrealized_pnl);
                  return (
                    <tr key={`${pos.symbol}-${i}`} className="border-b border-[rgba(255,255,255,0.03)] hover:bg-bg2/50">
                      <td className="py-2.5 px-4 font-mono text-text2 capitalize">{pos.exchange}</td>
                      <td className="py-2.5 px-4 font-mono font-semibold text-text">{pos.symbol}</td>
                      <td className="py-2.5 px-4">
                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${pos.side === "long" ? "bg-green-dim text-green" : "bg-red-dim text-red"}`}>
                          {pos.side === "long" ? "多" : "空"}
                        </span>
                      </td>
                      <td className="py-2.5 px-4 font-mono text-right text-text2">{formatNumber(pos.quantity, 4)}</td>
                      <td className="py-2.5 px-4 font-mono text-right text-text2">{formatNumber(pos.entry_price)}</td>
                      <td className="py-2.5 px-4 font-mono text-right text-text2">{formatNumber(pos.mark_price)}</td>
                      <td className={`py-2.5 px-4 font-mono text-right font-semibold ${pnl.color}`}>{pnl.text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </DashboardShell>
  );
}
