"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import DashboardShell from "@/components/layout/DashboardShell";
import {
  backtestApi,
  liveSessionApi,
  exchangeApi,
  type BacktestDetail,
  type BacktestTrade,
  type OHLCVPoint,
  type IndicatorData,
  type ExchangeAccount,
} from "@/lib/api";
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

  // ── Review ──
  const [reviewNotes, setReviewNotes] = useState("");
  const [reviewLoading, setReviewLoading] = useState(false);

  // ── Deploy ──
  const [deployOpen, setDeployOpen] = useState(false);
  const [deployLoading, setDeployLoading] = useState(false);
  const [deployMode, setDeployMode] = useState<"paper" | "live">("paper");
  const [deployAccountId, setDeployAccountId] = useState("");
  const [accounts, setAccounts] = useState<ExchangeAccount[]>([]);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [deploySuccess, setDeploySuccess] = useState<string | null>(null);

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

  // Fetch exchange accounts when deploy modal opens
  useEffect(() => {
    if (!deployOpen) return;
    exchangeApi.getAccounts().then(setAccounts);
  }, [deployOpen]);

  const handleReview = async (approved: boolean) => {
    setReviewLoading(true);
    try {
      await backtestApi.review(id, { approved, notes: reviewNotes });
      setDetail(await backtestApi.getFullDetail(id));
      setReviewNotes("");
    } finally {
      setReviewLoading(false);
    }
  };

  const handleDeploy = async () => {
    setDeployLoading(true);
    setDeployError(null);
    setDeploySuccess(null);
    try {
      if (!deployAccountId) {
        setDeployError("请选择交易所账户");
        return;
      }
      const result = await liveSessionApi.create({
        backtest_result_id: id,
        mode: deployMode,
        exchange_account_id: deployAccountId,
      });
      setDeploySuccess(`会话已创建 (${deployMode === "paper" ? "模拟" : "实盘"} 模式)`);
      setTimeout(() => setDeployOpen(false), 1500);
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setDeployLoading(false);
    }
  };

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

      {/* Review & Deploy Section */}
      {detail && (
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl px-4 py-3 mb-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-[10px] text-text3 uppercase tracking-wider font-semibold">审核状态</span>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                detail.review_status === "approved"
                  ? "bg-green-dim text-green"
                  : detail.review_status === "rejected"
                    ? "bg-red-dim text-red"
                    : "bg-amber-dim text-amber"
              }`}
            >
              {detail.review_status === "approved" ? "已通过" : detail.review_status === "rejected" ? "已拒绝" : "待审核"}
            </span>
            {detail.reviewed_at && (
              <span className="text-[10px] text-text3">{fmtDate(detail.reviewed_at)}</span>
            )}
          </div>

          {/* Pending: show review form */}
          {detail.review_status === "pending" && (
            <div className="flex flex-col gap-2">
              <textarea
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
                placeholder="审核备注（可选）"
                className="bg-bg2 border border-[rgba(255,255,255,0.1)] rounded-lg px-3 py-2 text-xs text-text placeholder:text-text3 focus:outline-none focus:border-green/30 resize-none"
                rows={2}
              />
              <div className="flex gap-2">
                <button
                  onClick={() => handleReview(true)}
                  disabled={reviewLoading}
                  className="px-4 py-1.5 rounded-md text-xs font-semibold cursor-pointer border border-green/20 bg-green-dim text-green hover:bg-green-dim/80 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {reviewLoading ? "审核中..." : "通过审核"}
                </button>
                <button
                  onClick={() => handleReview(false)}
                  disabled={reviewLoading}
                  className="px-4 py-1.5 rounded-md text-xs font-semibold cursor-pointer border border-red/20 bg-red-dim text-red hover:bg-red-dim/80 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {reviewLoading ? "审核中..." : "拒绝审核"}
                </button>
              </div>
            </div>
          )}

          {/* Approved: show deploy button */}
          {detail.review_status === "approved" && (
            <div className="flex items-center gap-3">
              <button
                onClick={() => {
                  setDeployOpen(true);
                  setDeployError(null);
                  setDeploySuccess(null);
                }}
                className="px-4 py-1.5 rounded-md text-xs font-semibold cursor-pointer border border-green/20 bg-green-dim text-green hover:bg-green-dim/80"
              >
                部署策略
              </button>
              {detail.review_notes && (
                <span className="text-[10px] text-text3">备注：{detail.review_notes}</span>
              )}
            </div>
          )}

          {/* Rejected */}
          {detail.review_status === "rejected" && (
            <div className="text-[10px] text-text3">
              {detail.review_notes && <span>备注：{detail.review_notes}</span>}
            </div>
          )}
        </div>
      )}

      {/* Deploy Modal */}
      {deployOpen && detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-bg1 border border-[rgba(255,255,255,0.1)] rounded-xl w-full max-w-md p-6">
            <h3 className="text-sm font-bold mb-4">部署策略</h3>
            <p className="text-xs text-text3 mb-4">
              {detail.symbol} · {detail.timeframe} · {detail.strategy_name ?? "未命名策略"}
            </p>

            {/* Mode selection */}
            <div className="mb-4">
              <label className="text-[10px] text-text3 uppercase tracking-wider font-semibold block mb-2">
                运行模式
              </label>
              <div className="flex gap-2">
                <button
                  onClick={() => setDeployMode("paper")}
                  className={`flex-1 py-2 rounded-lg text-xs font-semibold border cursor-pointer transition-all ${
                    deployMode === "paper"
                      ? "border-blue/30 bg-blue-dim text-blue"
                      : "border-[rgba(255,255,255,0.07)] text-text3 hover:text-text"
                  }`}
                >
                  模拟交易
                </button>
                <button
                  onClick={() => setDeployMode("live")}
                  className={`flex-1 py-2 rounded-lg text-xs font-semibold border cursor-pointer transition-all ${
                    deployMode === "live"
                      ? "border-green/30 bg-green-dim text-green"
                      : "border-[rgba(255,255,255,0.07)] text-text3 hover:text-text"
                  }`}
                >
                  实盘交易
                </button>
              </div>
            </div>

            {/* Exchange account selection */}
            <div className="mb-4">
              <label className="text-[10px] text-text3 uppercase tracking-wider font-semibold block mb-2">
                交易所账户
              </label>
              <select
                value={deployAccountId}
                onChange={(e) => setDeployAccountId(e.target.value)}
                className="w-full bg-bg2 border border-[rgba(255,255,255,0.1)] rounded-lg px-3 py-2 text-xs text-text focus:outline-none focus:border-green/30"
              >
                <option value="">请选择...</option>
                {accounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.exchange} · {acc.label || acc.id.slice(0, 8)} {acc.testnet ? "(模拟)" : ""}
                  </option>
                ))}
              </select>
            </div>

            {/* Error / Success */}
            {deployError && (
              <div className="bg-red-dim/20 border border-red/30 rounded-lg px-3 py-2 mb-4 text-xs text-red">
                {deployError}
              </div>
            )}
            {deploySuccess && (
              <div className="bg-green-dim/20 border border-green/30 rounded-lg px-3 py-2 mb-4 text-xs text-green">
                {deploySuccess}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setDeployOpen(false)}
                disabled={deployLoading}
                className="px-4 py-1.5 rounded-md text-xs font-semibold cursor-pointer border border-[rgba(255,255,255,0.1)] text-text3 hover:text-text disabled:opacity-40"
              >
                取消
              </button>
              <button
                onClick={handleDeploy}
                disabled={deployLoading}
                className="px-4 py-1.5 rounded-md text-xs font-semibold cursor-pointer border border-green/20 bg-green-dim text-green hover:bg-green-dim/80 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {deployLoading ? "创建中..." : "确认部署"}
              </button>
            </div>
          </div>
        </div>
      )}
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
