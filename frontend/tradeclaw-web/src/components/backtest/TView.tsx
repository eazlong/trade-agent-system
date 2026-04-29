"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type UTCTimestamp,
  type Time,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
} from "lightweight-charts";
import type { OHLCVPoint, IndicatorData, BacktestTrade } from "@/lib/api";
import { HoverInfo } from "./HoverInfo";
import type { SeriesMarkerPosition, SeriesMarkerShape } from "lightweight-charts";

// ─── Types ───────────────────────────────────────────────────────────────────

interface TViewProps {
  ohlcv: OHLCVPoint[];
  indicators?: IndicatorData;
  trades: BacktestTrade[];
  timeframe?: string;
  availableTimeframes?: string[];
  onTimeframeChange?: (tf: string) => void;
  totalBars?: number;
  onLoadMore?: (start: number, end: number) => void;
}

interface IndicatorLegend {
  label: string;
  color: string;
}

/** Convert ISO timestamp to UTCTimestamp (seconds) */
function toUTCTime(ts: string): UTCTimestamp {
  return Math.floor(new Date(ts).getTime() / 1000) as UTCTimestamp;
}

/** Ensure OHLCV data is ascending (oldest-first).
 *  Deduplication is handled upstream in currentData to keep indicators in sync.
 */
function ensureAscending(data: OHLCVPoint[]): OHLCVPoint[] {
  if (data.length < 2) return data;
  const firstTs = toUTCTime(data[0].timestamp);
  const lastTs = toUTCTime(data[data.length - 1].timestamp);
  return firstTs > lastTs ? data.toReversed() : data;
}

/** Binary search for closest data point by UTC timestamp */
function binarySearchTime(data: OHLCVPoint[], targetTs: UTCTimestamp): OHLCVPoint | null {
  if (!data.length) return null;
  let left = 0;
  let right = data.length - 1;
  while (left < right) {
    const mid = Math.floor((left + right) / 2);
    if (toUTCTime(data[mid].timestamp) < targetTs) {
      left = mid + 1;
    } else {
      right = mid;
    }
  }
  if (left > 0) {
    const diffLeft = Math.abs(toUTCTime(data[left].timestamp) - targetTs);
    const diffPrev = Math.abs(toUTCTime(data[left - 1].timestamp) - targetTs);
    return diffPrev < diffLeft ? data[left - 1] : data[left];
  }
  return data[left];
}

/** Reverse all indicator arrays to match reversed OHLCV */
function reverseIndicators(data: IndicatorData): IndicatorData {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (Array.isArray(value)) {
      result[key] = value.toReversed();
    } else if (typeof value === "object" && value !== null) {
      const obj: Record<string, unknown> = {};
      for (const [k2, v2] of Object.entries(value)) {
        obj[k2] = Array.isArray(v2) ? v2.toReversed() : v2;
      }
      result[key] = obj;
    }
  }
  return result as IndicatorData;
}

/** Build trade markers for both entry and exit points */
function buildTradeMarkers(
  trades: BacktestTrade[],
  ohlcv: OHLCVPoint[]
): SeriesMarker<Time>[] {
  if (!ohlcv.length || !trades.length) return [];

  const times = ohlcv.map((p) => toUTCTime(p.timestamp));

  // Find closest OHLCV bar for a given timestamp
  const findClosestTime = (targetTs: UTCTimestamp): Time => {
    let minDist = Infinity;
    let closestIdx = 0;
    for (let i = 0; i < times.length; i++) {
      const dist = Math.abs(times[i] - targetTs);
      if (dist < minDist) {
        minDist = dist;
        closestIdx = i;
      }
    }
    return times[closestIdx];
  };

  const markers: SeriesMarker<Time>[] = [];

  for (const t of trades) {
    const isLong = t.side === "long";

    // Entry marker: green for long, red for short
    const entryTime = toUTCTime(t.entry_time);
    markers.push({
      time: findClosestTime(entryTime),
      position: (isLong ? "belowBar" : "aboveBar") as SeriesMarkerPosition,
      color: isLong ? "#00e676" : "#ff5252",
      shape: (isLong ? "arrowUp" : "arrowDown") as SeriesMarkerShape,
      text: `入场 ${isLong ? "多" : "空"}`,
    });

    // Exit marker: always red, with arrow pointing opposite to entry
    if (t.exit_time) {
      const exitTime = toUTCTime(t.exit_time);
      const pnl = t.pnl ? Number(t.pnl) : 0;
      markers.push({
        time: findClosestTime(exitTime),
        position: (isLong ? "aboveBar" : "belowBar") as SeriesMarkerPosition,
        color: "#ff5252", // 出场标示显示为红色
        shape: (isLong ? "arrowDown" : "arrowUp") as SeriesMarkerShape,
        text: pnl >= 0 ? `出场 +${pnl.toFixed(0)}` : `出场 ${pnl.toFixed(0)}`,
      });
    }
  }

  // Sort by time
  return markers.sort((a, b) => {
    const ta = a.time as number;
    const tb = b.time as number;
    return ta - tb;
  });
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function TView({
  ohlcv,
  indicators,
  trades,
  timeframe,
  availableTimeframes = ["1m", "5m", "15m", "1h", "4h", "1d"],
  onTimeframeChange,
  totalBars: totalBarsProp,
  onLoadMore,
}: TViewProps) {
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartWrapperRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  // Track last requested range to prevent duplicate lazy-load requests
  const requestedEndRef = useRef(0);
  const isLoadingRef = useRef(false);
  const normalizedDataRef = useRef<OHLCVPoint[]>([]);
  const hoverInfoRef = useRef<any>(null);

  const totalBars = totalBarsProp ?? ohlcv.length;

  const [isFullscreen, setIsFullscreen] = useState(false);

  const timeframeRef = useRef(timeframe);
  const visibleRangeRef = useRef<{ from: Time; to: Time } | null>(null);
  const ohlcvRef = useRef(ohlcv);
  const onLoadMoreRef = useRef(onLoadMore);
  const totalBarsRef = useRef(totalBars);

  useEffect(() => {
    timeframeRef.current = timeframe;
  }, [timeframe]);
  useEffect(() => {
    ohlcvRef.current = ohlcv;
  }, [ohlcv]);
  useEffect(() => {
    onLoadMoreRef.current = onLoadMore;
  }, [onLoadMore]);
  useEffect(() => {
    totalBarsRef.current = totalBars;
  }, [totalBars]);

  // ── Crosshair handler for HoverInfo ───────────────────────────────────

  const handleCrosshairMove = useCallback((param: any) => {
    if (!param || !param.time || !hoverInfoRef.current) {
      hoverInfoRef.current?.setHoverData(null);
      return;
    }
    const d = normalizedDataRef.current;
    if (!d.length) {
      hoverInfoRef.current.setHoverData(null);
      return;
    }
    const targetTs = typeof param.time === "number"
      ? (param.time as UTCTimestamp)
      : toUTCTime(param.time);
    const closest = binarySearchTime(d, targetTs);
    if (!closest) {
      hoverInfoRef.current.setHoverData(null);
      return;
    }
    const change = closest.close - closest.open;
    const changePercent = (change / closest.open) * 100;
    hoverInfoRef.current.setHoverData({
      timestamp: toUTCTime(closest.timestamp),
      open: closest.open,
      high: closest.high,
      low: closest.low,
      close: closest.close,
      volume: closest.volume,
      change,
      changePercent,
      time: new Date(closest.timestamp).toLocaleString(),
    });
  }, []);

  // ── Build indicator legend ───────────────────────────────────────────────

  const legendItems = useMemo<IndicatorLegend[]>(() => {
    const items: IndicatorLegend[] = [];
    if (!indicators) return items;
    if (indicators.ma7?.length) items.push({ label: "MA(7)", color: "#f59e0b" });
    if (indicators.ma25?.length) items.push({ label: "MA(25)", color: "#3b82f6" });
    if (indicators.ma99?.length) items.push({ label: "MA(99)", color: "#a855f7" });
    if (indicators.macd?.dif?.length) items.push({ label: "MACD(12,26,9)", color: "#3b82f6" });
    if (indicators.rsi?.length) items.push({ label: "RSI(14)", color: "#a855f7" });
    items.push({ label: "VOL", color: "#6b7280" });
    return items;
  }, [indicators]);

  // ── Create chart ─────────────────────────────────────────────────────────

  useEffect(() => {
    if (!chartContainerRef.current || !ohlcv?.length) return;

    // Initialize refs: reset loading lock and requested end to current data length
    isLoadingRef.current = false;
    requestedEndRef.current = ohlcv.length;

    // Destroy previous chart
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const container = chartContainerRef.current;
    const width = container.clientWidth;
    const height = isFullscreen
      ? window.innerHeight - 64
      : Math.max(container.clientHeight - 64, 400);

    // Normalize to ascending order for the charting library
    const data = ensureAscending(ohlcv);
    const wasDescending = ohlcv.length >= 2 && toUTCTime(ohlcv[0].timestamp) > toUTCTime(ohlcv[ohlcv.length - 1].timestamp);
    const ind = wasDescending ? reverseIndicators(indicators ?? {}) : indicators;

    const chart = createChart(container, {
      width,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#0a0e27" },
        textColor: "#a0b0d0",
      },
      grid: {
        vertLines: { color: "rgba(0, 212, 255, 0.06)" },
        horzLines: { color: "rgba(0, 212, 255, 0.06)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "rgba(0, 212, 255, 0.5)",
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: "rgba(0, 212, 255, 0.8)",
        },
        horzLine: {
          color: "rgba(0, 212, 255, 0.5)",
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: "rgba(0, 212, 255, 0.8)",
        },
      },
      timeScale: {
        borderColor: "rgba(0, 212, 255, 0.2)",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "rgba(0, 212, 255, 0.2)",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      localization: {
        timeFormatter: (time: number) => {
          const d = new Date(time * 1000);
          const mm = String(d.getMonth() + 1).padStart(2, "0");
          const dd = String(d.getDate()).padStart(2, "0");
          const hh = String(d.getHours()).padStart(2, "0");
          const min = String(d.getMinutes()).padStart(2, "0");
          return `${mm}-${dd} ${hh}:${min}`;
        },
      },
    });

    // ── Main pane: Candlestick + MA lines ──
    const candles = chart.addCandlestickSeries({
      upColor: "#00d4ff",
      downColor: "#ef5350",
      borderUpColor: "#00d4ff",
      borderDownColor: "#ef5350",
      wickUpColor: "#00d4ff",
      wickDownColor: "#ef5350",
    });

    const candleData = data.map((p) => ({
      time: toUTCTime(p.timestamp),
      open: p.open,
      high: p.high,
      low: p.low,
      close: p.close,
    }));
    candles.setData(candleData);

    // Trade markers
    const markers = buildTradeMarkers(trades, data);
    if (markers.length) {
      candles.setMarkers(markers);
    }

    candleSeriesRef.current = candles;

    // ── HoverInfo plugin ──
    normalizedDataRef.current = data;
    const hoverInfo = new (HoverInfo as any)(chart, candles);
    candles.attachPrimitive(hoverInfo);
    hoverInfoRef.current = hoverInfo;

    // MA overlay lines
    const maColors: Record<string, string> = {
      ma7: "#f59e0b",
      ma25: "#3b82f6",
      ma99: "#a855f7",
    };

    (["ma7", "ma25", "ma99"] as const).forEach((key) => {
      const values = ind?.[key];
      if (!values || values.length === 0) return;
      const line = chart.addLineSeries({
        color: maColors[key],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const lineData = data
        .map((p, i) =>
          values[i] != null
            ? { time: toUTCTime(p.timestamp), value: values[i] }
            : null
        )
        .filter((d) => d !== null);
      line.setData(lineData);
    });

    // ── Sub-pane 1: Volume ──
    const volSeries = chart.addHistogramSeries({
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });
    volSeries.setData(
      data.map((p) => ({
        time: toUTCTime(p.timestamp),
        value: p.volume,
        color: p.close >= p.open ? "rgba(0,230,118,0.3)" : "rgba(255,82,82,0.3)",
      }))
    );
    volumeSeriesRef.current = volSeries;

    // ── Sub-pane 2: MACD or RSI ──
    if (ind?.macd?.dif?.length) {
      const macdSeries = chart.addLineSeries({
        color: "#3b82f6",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: "macd",
      });
      chart.priceScale("macd").applyOptions({
        scaleMargins: { top: 0.1, bottom: 0.1 },
      });

      const macdData = data
        .map((p, i) =>
          ind.macd!.dif[i] != null
            ? { time: toUTCTime(p.timestamp), value: ind.macd!.dif[i] }
            : null
        )
        .filter((d) => d !== null);
      macdSeries.setData(macdData);

      if (ind.macd.dea?.length) {
        const deaSeries = chart.addLineSeries({
          color: "#f59e0b",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          priceScaleId: "macd",
        });
        const deaData = data
          .map((p, i) =>
            ind.macd!.dea![i] != null
              ? { time: toUTCTime(p.timestamp), value: ind.macd!.dea![i] }
              : null
          )
          .filter((d) => d !== null);
        deaSeries.setData(deaData);
      }
    } else if (ind?.rsi?.length) {
      const rsiSeries = chart.addLineSeries({
        color: "#a855f7",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: "rsi",
      });
      chart.priceScale("rsi").applyOptions({
        scaleMargins: { top: 0.1, bottom: 0.1 },
      });

      const rsiData = data
        .map((p, i) =>
          ind.rsi![i] != null
            ? { time: toUTCTime(p.timestamp), value: ind.rsi![i] }
            : null
        )
        .filter((d) => d !== null);
      rsiSeries.setData(rsiData);
    }

    // ── Lazy loading on scroll to edge ─────────────────────────────────────

    const handleVisibleRangeChange = (range: { from: Time; to: Time } | null) => {
      if (!range) return;
      const total = totalBarsRef.current;
      const loadCb = onLoadMoreRef.current;
      if (!total || !loadCb) return;
      if (isLoadingRef.current) return;

      const normalizedData = ensureAscending(ohlcvRef.current);
      const loadedCount = normalizedData.length;
      if (loadedCount >= total) return;

      // Calculate bar interval from adjacent timestamps
      let barInterval = 60;
      if (normalizedData.length >= 2) {
        const t0 = toUTCTime(normalizedData[0].timestamp);
        const t1 = toUTCTime(normalizedData[1].timestamp);
        barInterval = Math.abs(t1 - t0) || 60;
      }
      const timeThreshold = barInterval * 100;

      const oldestLoadedTs = toUTCTime(normalizedData[0].timestamp);
      const newestLoadedTs = toUTCTime(normalizedData[normalizedData.length - 1].timestamp);

      const fromTs = typeof range.from === "number" ? (range.from as number)
          : typeof range.from === "string" ? Math.floor(new Date(range.from).getTime() / 1000)
          : 0;
      const toTs = typeof range.to === "number" ? (range.to as number)
          : typeof range.to === "string" ? Math.floor(new Date(range.to).getTime() / 1000)
          : 0;

      // Right edge: user scrolls toward newer data → load more recent bars
      const needLaterData = toTs >= newestLoadedTs - timeThreshold;
      // Left edge: user scrolls toward older data (reserved for bidirectional loading)
      void (fromTs <= oldestLoadedTs + timeThreshold);

      if (needLaterData && loadedCount < total) {
        const nextEnd = Math.min(loadedCount + 200, total);
        if (nextEnd > loadedCount && nextEnd > requestedEndRef.current) {
          requestedEndRef.current = nextEnd;
          isLoadingRef.current = true;
          loadCb(loadedCount, nextEnd);
          // Safety: reset loading lock after 10s in case data never arrives
          setTimeout(() => {
            isLoadingRef.current = false;
          }, 10000);
        }
      }

      visibleRangeRef.current = range;
    };

    chart
      .timeScale()
      .subscribeVisibleTimeRangeChange(handleVisibleRangeChange);

    // Hover info crosshair subscription
    chart.subscribeCrosshairMove(handleCrosshairMove);

    // Only fit content if all data is already loaded; otherwise show the
    // rightmost ~100 bars so the user can scroll right to trigger lazy loads.
    const total = totalBarsRef.current;
    if (!total || data.length >= total) {
      chart.timeScale().fitContent();
    } else {
      // Show roughly the newest 100 bars, leaving scroll room on right
      const visibleBars = Math.min(100, data.length);
      const fromIdx = data.length - visibleBars;
      chart.timeScale().setVisibleRange({
        from: toUTCTime(data[fromIdx].timestamp),
        to: toUTCTime(data[data.length - 1].timestamp),
      });
    }

    // Auto-resize
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
    });
    ro.observe(container);

    chartRef.current = chart;

    return () => {
      ro.disconnect();
      try {
        chart.unsubscribeCrosshairMove(handleCrosshairMove);
      } catch {}
      try {
        chart
          .timeScale()
          .unsubscribeVisibleTimeRangeChange(handleVisibleRangeChange);
      } catch {}
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      hoverInfoRef.current = null;
    };
  }, [ohlcv, indicators, trades, isFullscreen]);

  // ── Fullscreen toggle ────────────────────────────────────────────────────

  const toggleFullscreen = useCallback(async () => {
    if (!document.fullscreenElement) {
      try {
        await chartWrapperRef.current?.requestFullscreen();
        setIsFullscreen(true);
      } catch (err) {
        console.error("Enter fullscreen failed:", err);
      }
    } else {
      try {
        await document.exitFullscreen();
        setIsFullscreen(false);
      } catch (err) {
        console.error("Exit fullscreen failed:", err);
      }
    }
  }, []);

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // ── Render ───────────────────────────────────────────────────────────────

  if (!ohlcv?.length) {
    return (
      <div className="h-40 bg-bg2 rounded-lg animate-pulse flex items-center justify-center text-xs text-text3">
        暂无 K 线数据
      </div>
    );
  }

  return (
    <div
      ref={chartWrapperRef}
      className="relative flex flex-col w-full overflow-hidden bg-[#0a0e27]"
      style={{ height: isFullscreen ? "100vh" : "auto" }}
    >
      {/* Control bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-2 py-1 text-[10px]">
        {/* Timeframe switcher */}
        <div className="flex gap-1">
          {availableTimeframes.map((tf) => {
            const isActive = timeframe === tf;
            return (
              <button
                key={tf}
                onClick={() => onTimeframeChange?.(tf)}
                className={`px-2 py-0.5 font-medium rounded cursor-pointer transition-all border ${
                  isActive
                    ? "text-green bg-green-dim border-green/20"
                    : "text-text3 border-transparent hover:text-text hover:bg-bg2"
                }`}
              >
                {tf}
              </button>
            );
          })}
        </div>

        {/* Indicator legend */}
        <div className="flex flex-wrap gap-3 text-text3">
          {legendItems.map((item) => (
            <span key={item.label} className="flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: item.color }}
              />
              {item.label}
            </span>
          ))}
        </div>

        {/* Fullscreen button */}
        <button
          className="px-2 py-0.5 text-[10px] font-medium rounded border border-[#00d4ff]/30 text-[#00d4ff] cursor-pointer hover:bg-[#00d4ff]/10 transition-all"
          onClick={toggleFullscreen}
        >
          {isFullscreen ? "退出全屏" : "全屏"}
        </button>
      </div>

      {/* Chart container */}
      <div ref={chartContainerRef} className="w-full" />
    </div>
  );
}
