"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import DashboardShell from "@/components/layout/DashboardShell";
import { backtestApi, type BacktestResult } from "@/lib/api";

export default function BacktestListPage() {
  const [results, setResults] = useState<BacktestResult[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    backtestApi
      .getList()
      .then(setResults)
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, []);

  const fmtDate = (d: string) =>
    new Date(d).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });

  return (
    <DashboardShell>
      <div className="mb-4">
        <h1 className="text-lg font-bold text-text">回测记录</h1>
        <p className="text-xs text-text3 mt-1">查看历史回测结果与详细分析</p>
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
    </DashboardShell>
  );
}
