"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import DashboardShell from "@/components/layout/DashboardShell";
import { backtestApi, type BacktestDetail, type BacktestTrade } from "@/lib/api";
import CandlestickChart from "@/components/backtest/CandlestickChart";
import EquityChart from "@/components/backtest/EquityChart";
import DrawdownChart from "@/components/backtest/DrawdownChart";
import TradeLog from "@/components/backtest/TradeLog";

type TabKey = "kline" | "equity" | "drawdown" | "trades";

export default function BacktestDetailPage() {
  const params = useParams();
  const id = params.id as string;

  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [trades, setTrades] = useState<BacktestTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>("kline");

  useEffect(() => {
    backtestApi
      .getFullDetail(id)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!detail) return;
    // Fetch all trades for markers (large page_size)
    backtestApi.getTrades(id, { page: 1, page_size: 10000 }).then((res) => {
      setTrades(res.results);
    });
  }, [detail, id]);

  if (loading) {
    return (
      <DashboardShell>
        <div className="text-xs text-text3 py-8 text-center">加载中...</div>
      </DashboardShell>
    );
  }

  if (!detail) {
    return (
      <DashboardShell>
        <div className="text-xs text-text3 py-8 text-center">
          回测记录不存在
          <Link href="/backtest" className="text-green hover:underline ml-2">
            返回列表
          </Link>
        </div>
      </DashboardShell>
    );
  }

  const fmtDate = (d: string) =>
    new Date(d).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });

  const fmtNum = (v: number | null, decimals = 2) =>
    v !== null ? v.toFixed(decimals) : "—";

  const tabs: { key: TabKey; label: string }[] = [
    { key: "kline", label: "K线图" },
    { key: "equity", label: "权益曲线" },
    { key: "drawdown", label: "回撤曲线" },
    { key: "trades", label: "交易日志" },
  ];

  return (
    <DashboardShell>
      {/* Back + Header */}
      <div className="mb-4">
        <Link href="/backtest" className="text-xs text-text3 hover:text-text mb-2 inline-block cursor-pointer">
          ← 返回回测列表
        </Link>
        <h1 className="text-lg font-bold text-text">
          {detail.symbol} · {detail.timeframe}
        </h1>
        <p className="text-xs text-text3 mt-0.5">
          {fmtDate(detail.start_date)} ~ {fmtDate(detail.end_date)}
        </p>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-6 gap-3 mb-4">
        {[
          { label: "初始资金", value: Number(detail.initial_capital).toLocaleString() },
          { label: "最终资金", value: Number(detail.final_capital).toLocaleString() },
          {
            label: "总收益率",
            value: `${detail.total_return_pct >= 0 ? "+" : ""}${detail.total_return_pct.toFixed(2)}%`,
            color: detail.total_return_pct >= 0 ? "text-green" : "text-red",
          },
          { label: "夏普比率", value: fmtNum(detail.sharpe_ratio) },
          {
            label: "最大回撤",
            value: fmtNum(detail.max_drawdown_pct) + "%",
            color: "text-red",
          },
          {
            label: "胜率",
            value: detail.win_rate ? `${detail.win_rate.toFixed(1)}%` : "—",
          },
        ].map((m, i) => (
          <div
            key={i}
            className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl px-4 py-3"
          >
            <div className="text-[10px] text-text3 uppercase tracking-wider mb-1">
              {m.label}
            </div>
            <div className={`text-sm font-mono font-bold ${m.color ?? "text-text"}`}>
              {m.value}
            </div>
          </div>
        ))}
      </div>

      {/* Tab Switcher */}
      <div className="flex gap-1 mb-3">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-1.5 text-xs font-medium rounded-lg cursor-pointer transition-all border ${
              activeTab === tab.key
                ? "text-green bg-green-dim border-green/20"
                : "text-text2 border-transparent hover:text-text hover:bg-bg2"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Chart / Log Area */}
      <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl px-4 py-4">
        {activeTab === "kline" && detail && (
          <CandlestickChart
            ohlcv={detail.ohlcv_data}
            indicators={detail.indicator_data}
            trades={trades}
          />
        )}
        {activeTab === "equity" && (
          <EquityChart data={detail.equity_curve} showBenchmark />
        )}
        {activeTab === "drawdown" && (
          <DrawdownChart data={detail.drawdown_curve} />
        )}
        {activeTab === "trades" && <TradeLog backtestId={id} />}
      </div>

      {/* Parameters */}
      {detail.parameters && Object.keys(detail.parameters).length > 0 && (
        <div className="mt-4 bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl px-4 py-3">
          <div className="text-[10px] text-text3 uppercase tracking-wider mb-2">
            策略参数
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(detail.parameters).map(([k, v]) => (
              <span
                key={k}
                className="text-xs font-mono bg-bg2 border border-[rgba(255,255,255,0.07)] rounded px-2 py-1 text-text2"
              >
                {k} = {String(v)}
              </span>
            ))}
          </div>
        </div>
      )}
    </DashboardShell>
  );
}
