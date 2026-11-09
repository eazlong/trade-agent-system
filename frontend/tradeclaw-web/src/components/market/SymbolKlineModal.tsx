"use client";

/**
 * 顶栏行情条（`layout/Topbar.tsx`）点击某个品种后弹出的 K 线对话框。
 *
 * 取数只用 `tradingApi.getKlines` —— 也就是 `GET /api/trading/klines/?symbol=&interval=&limit=`：
 * 后端 `apps/trading/views.py::klines` 直接经代理从交易所拉 OHLCV，**与交易记录弹窗
 * （`trading/TradeRecords.tsx`）是同一个口**。不在前端另拼行情源，也不拿顶栏那条
 * ticker 的价格去"补"K 线：ticker 只有最新价，它拼不出一根 K。
 *
 * K 线本体交给既有的 `backtest/TView`（lightweight-charts + 成交量 + 十字光标）。
 * 这个弹窗只负责「品种 / 周期 / 取数 / 失败如实报错 / 关掉」这几件事。
 *
 * 取数结果按 `symbol|interval` 打标：**当前请求键与结果键不一致就是还在加载**。
 * 这样切周期时既不会先闪一帧旧周期的图，也不需要靠 effect 里的同步 setState
 * （那会触发级联渲染，`react-hooks/set-state-in-effect` 会判红）。
 */

import { useEffect, useState } from "react";
import { tradingApi, type OHLCVPoint } from "@/lib/api";
import TView from "@/components/backtest/TView";

/** 与后端 `KLINE_INTERVALS`（`apps/trading/views.py`）逐字一致：多给一个取值后端就 400。 */
export const KLINE_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];
export const DEFAULT_INTERVAL = "1h";
export const KLINE_LIMIT = 500;

interface KlineResult {
  /** 这份结果是哪个 `symbol|interval` 的请求产生的 */
  key: string;
  data: OHLCVPoint[];
  error: string | null;
}

export default function SymbolKlineModal({
  symbol,
  onClose,
}: {
  symbol: string;
  onClose: () => void;
}) {
  const [interval, setInterval] = useState(DEFAULT_INTERVAL);
  const [result, setResult] = useState<KlineResult | null>(null);

  const requestKey = `${symbol}|${interval}`;

  // Esc 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // 取 K 线（切周期 / 换品种都重取）。`stale` 防乱序响应覆盖新周期。
  useEffect(() => {
    let stale = false;
    const key = `${symbol}|${interval}`;
    tradingApi
      .getKlines(symbol, interval, KLINE_LIMIT)
      .then((res) => {
        if (!stale) setResult({ key, data: res.ohlcv_data || [], error: null });
      })
      .catch((e) => {
        if (!stale)
          setResult({
            key,
            data: [],
            error: e instanceof Error ? e.message : "K 线加载失败",
          });
      });
    return () => {
      stale = true;
    };
  }, [symbol, interval]);

  const fresh = result && result.key === requestKey ? result : null;
  const loading = !fresh;
  const ohlcv = fresh?.data ?? [];
  const error = fresh?.error ?? null;

  const lastBar = ohlcv.length ? ohlcv[ohlcv.length - 1] : null;
  const changePct = lastBar
    ? ((lastBar.close - lastBar.open) / lastBar.open) * 100
    : null;

  return (
    <div
      className="fixed inset-0 bg-black/70 z-[100] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={onClose}
      data-testid="kline-modal"
    >
      <div
        className="bg-bg1 border border-[rgba(255,255,255,0.1)] rounded-xl w-full max-w-5xl max-h-[90vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between gap-3 flex-shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs font-bold text-text flex-shrink-0">K 线</span>
            <span
              className="font-mono text-[11px] font-semibold text-text2 truncate"
              data-testid="kline-symbol"
            >
              {symbol}
            </span>
            {lastBar && (
              <span className="font-mono text-[11px] font-semibold text-text flex-shrink-0">
                {lastBar.close}
              </span>
            )}
            {lastBar && changePct !== null && (
              <span
                className={`font-mono text-[10px] font-semibold flex-shrink-0 ${
                  changePct >= 0 ? "text-green" : "text-red"
                }`}
              >
                {changePct >= 0 ? "+" : ""}
                {changePct.toFixed(2)}%
              </span>
            )}
            {error && (
              <span
                className="text-[10px] text-red truncate"
                data-testid="kline-error"
              >
                {error}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text3 hover:text-text hover:bg-bg2 cursor-pointer transition-all flex-shrink-0"
            aria-label="关闭"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path
                d="M2 2 L10 10 M10 2 L2 10"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* Chart */}
        <div className="px-2 py-2 overflow-hidden">
          <TView
            ohlcv={ohlcv}
            loading={loading}
            timeframe={interval}
            availableTimeframes={KLINE_INTERVALS}
            onTimeframeChange={setInterval}
          />
        </div>

        <div className="px-4 pb-2 text-[9px] text-text3 flex-shrink-0">
          {symbol} · 数据源 Binance · 最近 {KLINE_LIMIT} 根
        </div>
      </div>
    </div>
  );
}
