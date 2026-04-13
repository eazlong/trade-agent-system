"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import DashboardShell from "@/components/layout/DashboardShell";
import { backtestApi, type BacktestDetail, type BacktestTrade, type OHLCVPoint, type IndicatorData } from "@/lib/api";
import { resampleOHLCV, computeIndicators, timeframeToMinutes } from "@/lib/resample";
import CandlestickChart from "@/components/backtest/CandlestickChart";
import EquityChart from "@/components/backtest/EquityChart";
import DrawdownChart from "@/components/backtest/DrawdownChart";
import TradeLog from "@/components/backtest/TradeLog";

/** Timeframes available for client-side resampling */
const RESAMPLE_TARGETS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

type TabKey = "kline" | "equity" | "drawdown" | "trades";

interface CachedTimeframeData {
  ohlcv: OHLCVPoint[];
  indicators: IndicatorData;
}

export default function BacktestDetailPage() {
  const params = useParams();
  const id = params.id as string;

  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [trades, setTrades] = useState<BacktestTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>("kline");

  // ── Timeframe switching with cache ──
  const [activeTf, setActiveTf] = useState<string | null>(null);
  const [tfCache, setTfCache] = useState<Record<string, CachedTimeframeData>>({});
  const [tfLoading, setTfLoading] = useState(false);

  /** Resample data for target timeframe, using cache if available */
  const switchTimeframe = useCallback(
    (targetTf: string) => {
      if (!detail) return;

      // Check cache first
      if (tfCache[targetTf]) {
        setActiveTf(targetTf);
        return;
      }

      // Resample synchronously (fast for typical dataset sizes < 10k bars)
      setTfLoading(true);
      // Use requestAnimationFrame to show loading spinner for at least one frame
      requestAnimationFrame(() => {
        const ohlcv = resampleOHLCV(detail.ohlcv_data, detail.timeframe, targetTf);
        const indicators = computeIndicators(ohlcv, detail.indicator_data);
        setTfCache((prev) => ({ ...prev, [targetTf]: { ohlcv, indicators } }));
        setActiveTf(targetTf);
        setTfLoading(false);
      });
    },
    [detail, tfCache]
  );

  // Initialize active timeframe from detail
  useEffect(() => {
    if (detail && !activeTf) {
      setActiveTf(detail.timeframe);
      // Pre-cache the base timeframe
      setTfCache((prev) => ({
        ...prev,
        [detail.timeframe]: {
          ohlcv: detail.ohlcv_data,
          indicators: detail.indicator_data,
        },
      }));
    }
  }, [detail, activeTf]);

  /** Current OHLCV + indicators for the active timeframe */
  const currentData = useMemo((): { ohlcv: OHLCVPoint[]; indicators: IndicatorData } => {
    if (!activeTf || !detail) return { ohlcv: [], indicators: {} };
    // Base timeframe: use original data
    if (activeTf === detail.timeframe) {
      return { ohlcv: detail.ohlcv_data, indicators: detail.indicator_data };
    }
    // Resampled timeframe: use cache
    return tfCache[activeTf] ?? { ohlcv: [], indicators: {} };
  }, [activeTf, detail, tfCache]);

  // Determine which timeframes to show (only >= base timeframe)
  const availableTimeframes = useMemo(() => {
    if (!detail) return RESAMPLE_TARGETS;
    const baseMin = timeframeToMinutes(detail.timeframe);
    return RESAMPLE_TARGETS.filter((tf) => timeframeToMinutes(tf) >= baseMin);
  }, [detail]);

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
          <>
            {tfLoading && activeTf && (
              <div className="text-xs text-text3 py-4 text-center animate-pulse">
                聚合 {activeTf} K 线数据...
              </div>
            )}
            {!tfLoading && (
              <CandlestickChart
                ohlcv={currentData.ohlcv}
                indicators={currentData.indicators}
                trades={trades}
                timeframe={activeTf}
                availableTimeframes={availableTimeframes}
                onTimeframeChange={switchTimeframe}
              />
            )}
          </>
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
