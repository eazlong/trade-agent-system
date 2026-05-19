"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import DashboardShell from "@/components/layout/DashboardShell";
import { backtestApi, type BacktestResult } from "@/lib/api";
import CreateStrategyModal from "@/components/dashboard/CreateStrategyModal";

const PAGE_SIZE = 20;

export default function BacktestListPage() {
  const [results, setResults] = useState<BacktestResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    setLoading(true);
    backtestApi
      .getList({ page, page_size: PAGE_SIZE })
      .then((data) => {
        setResults(data.results ?? []);
        setTotalPages(data.num_pages);
        setTotalCount(data.count);
      })
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [page]);

  const fmtDate = (d: string) =>
    new Date(d).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });

  return (
    <DashboardShell>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-text">回测记录</h1>
          <p className="text-xs text-text3 mt-1">查看历史回测结果与详细分析</p>
        </div>
        <button
          onClick={() => setModalOpen(true)}
          className="bg-green text-black text-xs font-semibold px-4 py-2 rounded-lg shadow-lg hover:opacity-85 transition-all cursor-pointer"
        >
          + 新建策略
        </button>
      </div>

      {loading ? (
        <div className="text-xs text-text3 py-8 text-center">加载中...</div>
      ) : results.length === 0 ? (
        <div className="text-xs text-text3 py-8 text-center">
          暂无回测记录
        </div>
      ) : (
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-text3 border-b border-[rgba(255,255,255,0.07)]">
                <th className="text-left py-2.5 px-4 font-semibold">策略</th>
                <th className="text-left py-2.5 px-4 font-semibold">品种</th>
                <th className="text-left py-2.5 px-4 font-semibold">周期</th>
                <th className="text-left py-2.5 px-4 font-semibold">日期区间</th>
                <th className="text-right py-2.5 px-4 font-semibold">收益率</th>
                <th className="text-right py-2.5 px-4 font-semibold">夏普</th>
                <th className="text-right py-2.5 px-4 font-semibold">最大回撤</th>
                <th className="text-right py-2.5 px-4 font-semibold">交易数</th>
                <th className="text-right py-2.5 px-4 font-semibold">胜率</th>
                <th className="text-right py-2.5 px-4 font-semibold">操作</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr
                  key={r.id}
                  className="border-b border-[rgba(255,255,255,0.04)] hover:bg-bg2/50 transition-colors"
                >
                  <td className="py-2 px-4 text-text font-semibold">{r.strategy_name}</td>
                  <td className="py-2 px-4 text-text font-semibold">{r.symbol}</td>
                  <td className="py-2 px-4 text-text2">{r.timeframe}</td>
                  <td className="py-2 px-4 text-text3">
                    {fmtDate(r.start_date)} ~ {fmtDate(r.end_date)}
                  </td>
                  <td
                    className={`py-2 px-4 text-right font-semibold ${
                      r.total_return_pct >= 0 ? "text-green" : "text-red"
                    }`}
                  >
                    {r.total_return_pct >= 0 ? "+" : ""}
                    {r.total_return_pct.toFixed(2)}%
                  </td>
                  <td className="py-2 px-4 text-right text-text">
                    {r.sharpe_ratio?.toFixed(2) ?? "—"}
                  </td>
                  <td className="py-2 px-4 text-right text-red">
                    {r.max_drawdown_pct?.toFixed(2) ?? "—"}%
                  </td>
                  <td className="py-2 px-4 text-right text-text2">
                    {r.total_trades}
                  </td>
                  <td className="py-2 px-4 text-right text-text2">
                    {r.win_rate ? `${(r.win_rate * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="py-2 px-4 text-right">
                    <Link
                      href={`/backtest/${r.id}`}
                      className="text-green hover:underline cursor-pointer"
                    >
                      详情
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && results.length > 0 && totalPages > 1 && (
        <div className="flex items-center justify-between mt-3 text-xs font-mono">
          <span className="text-text3">
            共 {totalCount} 条，第 {page}/{totalPages} 页
          </span>
          <div className="flex gap-1.5">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-2.5 py-1 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              上一页
            </button>
            {page > 2 && (
              <button
                onClick={() => setPage(1)}
                className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
              >
                1
              </button>
            )}
            {page > 3 && (
              <span className="px-1 py-1 text-text3">…</span>
            )}
            {page > 1 && (
              <button
                onClick={() => setPage(page - 1)}
                className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
              >
                {page - 1}
              </button>
            )}
            <span className="w-8 h-7 flex items-center justify-center rounded-md bg-green/10 border border-green/30 text-green font-semibold">
              {page}
            </span>
            {page < totalPages && (
              <button
                onClick={() => setPage(page + 1)}
                className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
              >
                {page + 1}
              </button>
            )}
            {page < totalPages - 2 && (
              <span className="px-1 py-1 text-text3">…</span>
            )}
            {page < totalPages - 1 && (
              <button
                onClick={() => setPage(totalPages)}
                className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
              >
                {totalPages}
              </button>
            )}
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="px-2.5 py-1 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              下一页
            </button>
          </div>
        </div>
      )}

      <CreateStrategyModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </DashboardShell>
  );
}
