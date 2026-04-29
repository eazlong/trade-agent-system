"use client";

import { useEffect, useState, useCallback } from "react";
import { backtestApi, type BacktestTrade } from "@/lib/api";

interface TradeLogProps {
  backtestId: string;
}

export default function TradeLog({ backtestId }: TradeLogProps) {
  const [trades, setTrades] = useState<BacktestTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [numPages, setNumPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [sort, setSort] = useState("entry_time");

  const fetchTrades = useCallback(
    async (p: number, s: string) => {
      setLoading(true);
      try {
        const res = await backtestApi.getTrades(backtestId, {
          page: p,
          page_size: 50,
          sort: s,
        });
        setTrades(res.results);
        setNumPages(res.num_pages);
        setTotalCount(res.count);
      } catch {
        setTrades([]);
      } finally {
        setLoading(false);
      }
    },
    [backtestId]
  );

  useEffect(() => {
    fetchTrades(page, sort);
  }, [page, sort, fetchTrades]);

  const handleSort = (field: string) => {
    const newSort = sort === field ? `-${field}` : field;
    setSort(newSort);
    setPage(1);
  };

  const sortIndicator = (field: string) => {
    if (sort === field) return " ↓";
    if (sort === `-${field}`) return " ↑";
    return "";
  };

  if (loading) {
    return (
      <div className="py-4 text-center text-xs text-text3">加载中...</div>
    );
  }

  if (!trades.length) {
    return (
      <div className="py-4 text-center text-xs text-text3">暂无交易记录</div>
    );
  }

  const fmtPrice = (v: string | null) =>
    v ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
  const fmtPnl = (v: string | null) =>
    v ? Number(v).toFixed(2) : "—";
  const fmtTime = (v: string | null) =>
    v
      ? new Date(v).toLocaleDateString("zh-CN", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";

  const tradeTypeLabel = (t: string) => {
    switch (t) {
      case "open":
        return "开仓";
      case "add":
        return "加仓";
      case "close":
        return "出场";
      default:
        return t;
    }
  };

  const tradeTypeClass = (t: string) => {
    switch (t) {
      case "open":
        return "bg-blue-dim text-blue";
      case "add":
        return "bg-amber-dim text-amber";
      case "close":
        return "bg-purple-dim text-purple";
      default:
        return "bg-bg2 text-text3";
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-2 text-[10px] text-text3">
        <span>共 {totalCount} 笔交易</span>
        <div className="flex gap-2">
          <span className="text-text2">排序:</span>
          <button
            onClick={() => handleSort("entry_time")}
            className="hover:text-text cursor-pointer"
          >
            时间{sortIndicator("entry_time")}
          </button>
          <button
            onClick={() => handleSort("pnl")}
            className="hover:text-text cursor-pointer"
          >
            盈亏{sortIndicator("pnl")}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-text3 border-b border-[rgba(255,255,255,0.07)]">
              <th className="text-left py-1.5 pr-2 font-semibold">#</th>
              <th className="text-left py-1.5 pr-2 font-semibold">类型</th>
              <th className="text-left py-1.5 pr-2 font-semibold">入场时间</th>
              <th className="text-left py-1.5 pr-2 font-semibold">出场时间</th>
              <th className="text-left py-1.5 pr-2 font-semibold">方向</th>
              <th className="text-right py-1.5 pr-2 font-semibold">入场价</th>
              <th className="text-right py-1.5 pr-2 font-semibold">出场价</th>
              <th className="text-right py-1.5 pr-2 font-semibold">数量</th>
              <th className="text-right py-1.5 pr-2 font-semibold">PnL</th>
              <th className="text-left py-1.5 pr-2 font-semibold">标签</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => {
              const isClose = t.trade_type === "close";
              const pnlValue = t.pnl ? Number(t.pnl) : null;
              return (
              <tr
                key={t.id}
                className={`border-b border-[rgba(255,255,255,0.04)] hover:bg-bg2/50 transition-colors ${
                  !isClose ? "opacity-70" : ""
                }`}
              >
                <td className="py-1.5 pr-2 text-text3">{i + 1}</td>
                <td className="py-1.5 pr-2">
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${tradeTypeClass(t.trade_type)}`}
                  >
                    {tradeTypeLabel(t.trade_type)}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-text">{fmtTime(t.entry_time)}</td>
                <td className="py-1.5 pr-2 text-text">{fmtTime(t.exit_time)}</td>
                <td className="py-1.5 pr-2">
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      t.side === "long"
                        ? "bg-green-dim text-green"
                        : "bg-red-dim text-red"
                    }`}
                  >
                    {t.side === "long" ? "多" : "空"}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-right text-text">
                  {t.entry_price ? fmtPrice(t.entry_price) : "—"}
                </td>
                <td className="py-1.5 pr-2 text-right text-text">
                  {t.exit_price ? fmtPrice(t.exit_price) : "—"}
                </td>
                <td className="py-1.5 pr-2 text-right text-text2">{t.quantity}</td>
                <td
                  className={`py-1.5 pr-2 text-right font-semibold ${
                    pnlValue !== null && pnlValue >= 0 ? "text-green" : pnlValue !== null ? "text-red" : "text-text3"
                  }`}
                >
                  {pnlValue !== null ? pnlValue.toFixed(2) : "—"}
                </td>
                <td className="py-1.5 pr-2">
                  <div className="flex gap-1 flex-wrap">
                    {t.signal && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-bg2 text-text3">
                        {t.signal}
                      </span>
                    )}
                    {t.exit_reason && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-bg2 text-text3">
                        {t.exit_reason}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {numPages > 1 && (
        <div className="flex items-center justify-between mt-3 text-xs">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="px-2.5 py-1 bg-bg2 border border-[rgba(255,255,255,0.07)] rounded hover:bg-bg3 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
          >
            ← 上一页
          </button>
          <span className="text-text3">
            {page} / {numPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(numPages, p + 1))}
            disabled={page >= numPages}
            className="px-2.5 py-1 bg-bg2 border border-[rgba(255,255,255,0.07)] rounded hover:bg-bg3 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
          >
            下一页 →
          </button>
        </div>
      )}
    </div>
  );
}
