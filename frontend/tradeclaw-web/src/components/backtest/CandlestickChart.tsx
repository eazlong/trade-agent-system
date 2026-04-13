"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import {
  createChart,
  ColorType,
  LineStyle,
  type UTCTimestamp,
  type DeepPartial,
  type ChartOptions,
  type SeriesMarker,
  type SeriesMarkerPosition,
  type SeriesMarkerShape,
} from "lightweight-charts";
import type { OHLCVPoint, IndicatorData, BacktestTrade } from "@/lib/api";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

interface CandlestickChartProps {
  ohlcv: OHLCVPoint[];
  indicators: IndicatorData;
  trades: BacktestTrade[];
  timeframe?: string;
  onTimeframeChange?: (tf: string) => void;
}

interface IndicatorLegend {
  label: string;
  color: string;
}

/** Convert ISO timestamp to UTCTimestamp (seconds) */
function toUTCTime(ts: string): UTCTimestamp {
  return Math.floor(new Date(ts).getTime() / 1000) as UTCTimestamp;
}

/** Build trade markers mapped to OHLCV timestamps */
function buildTradeMarkers(
  trades: BacktestTrade[],
  ohlcv: OHLCVPoint[]
): SeriesMarker<UTCTimestamp>[] {
  if (!ohlcv.length || !trades.length) return [];

  const times = ohlcv.map((p) => toUTCTime(p.timestamp));

  return trades
    .map((t) => {
      const entryTime = toUTCTime(t.entry_time);
      // Find closest OHLCV index
      let minDist = Infinity;
      let closestIdx = 0;
      for (let i = 0; i < times.length; i++) {
        const dist = Math.abs(times[i] - entryTime);
        if (dist < minDist) {
          minDist = dist;
          closestIdx = i;
        }
      }

      const pnl = t.pnl ? Number(t.pnl) : 0;
      const isLong = t.side === "long";

      return {
        time: times[closestIdx],
        position: (isLong ? "belowBar" : "aboveBar") as SeriesMarkerPosition,
        color: isLong ? "#00e676" : "#ff5252",
        shape: (isLong ? "arrowUp" : "arrowDown") as SeriesMarkerShape,
        text: pnl >= 0 ? `+${pnl.toFixed(0)}` : pnl.toFixed(0),
      };
    })
    .sort((a, b) => a.time - b.time);
}

export default function CandlestickChart({
  ohlcv,
  indicators,
  trades,
  timeframe,
  onTimeframeChange,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeTf, setActiveTf] = useState(timeframe || "15m");

  // Build indicator legend
  const legendItems = useMemo<IndicatorLegend[]>(() => {
    const items: IndicatorLegend[] = [];
    if (indicators.ma7?.length) items.push({ label: "MA(7)", color: "#f59e0b" });
    if (indicators.ma25?.length) items.push({ label: "MA(25)", color: "#3b82f6" });
    if (indicators.ma99?.length) items.push({ label: "MA(99)", color: "#a855f7" });
    if (indicators.macd?.dif?.length) items.push({ label: "MACD(12,26,9)", color: "#3b82f6" });
    if (indicators.rsi?.length) items.push({ label: "RSI(14)", color: "#a855f7" });
    items.push({ label: "VOL", color: "#6b7280" });
    return items;
  }, [indicators]);

  useEffect(() => {
    if (timeframe && timeframe !== activeTf) {
      setActiveTf(timeframe);
    }
  }, [timeframe]);

  useEffect(() => {
    if (!containerRef.current || !ohlcv.length) return;

    const chartOptions: DeepPartial<ChartOptions> = {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b95a8",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.03)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      crosshair: {
        mode: 1,
        vertLine: {
          color: "rgba(255,255,255,0.15)",
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: "#1a1f2e",
        },
        horzLine: {
          color: "rgba(255,255,255,0.15)",
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: "#1a1f2e",
        },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.07)",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.07)",
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    };

    const chart = createChart(containerRef.current, {
      ...chartOptions,
      height: 420,
    });

    // ── Main pane: Candlestick + MA lines ──
    const candleSeries = chart.addCandlestickSeries({
      upColor: "#00e676",
      downColor: "#ff5252",
      borderUpColor: "#00e676",
      borderDownColor: "#ff5252",
      wickUpColor: "#00e676",
      wickDownColor: "#ff5252",
    });

    const candleData = ohlcv.map((p) => ({
      time: toUTCTime(p.timestamp),
      open: p.open,
      high: p.high,
      low: p.low,
      close: p.close,
    }));
    candleSeries.setData(candleData);

    // Trade markers
    const markers = buildTradeMarkers(trades, ohlcv);
    if (markers.length) {
      candleSeries.setMarkers(markers);
    }

    // MA overlay lines
    const maColors: Record<string, string> = {
      ma7: "#f59e0b",
      ma25: "#3b82f6",
      ma99: "#a855f7",
    };

    (["ma7", "ma25", "ma99"] as const).forEach((key) => {
      const values = indicators[key];
      if (!values || values.length === 0) return;
      const line = chart.addLineSeries({
        color: maColors[key],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const data = ohlcv
        .map((p, i) =>
          values[i] != null
            ? { time: toUTCTime(p.timestamp), value: values[i] }
            : null
        )
        .filter((d) => d !== null);
      line.setData(data);
    });

    // ── Sub-pane 1: Volume ──
    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const volumeData = ohlcv.map((p) => ({
      time: toUTCTime(p.timestamp),
      value: p.volume,
      color: p.close >= p.open ? "rgba(0,230,118,0.3)" : "rgba(255,82,82,0.3)",
    }));
    volumeSeries.setData(volumeData);

    // ── Sub-pane 2: MACD or RSI ──
    if (indicators.macd?.dif?.length) {
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

      const macdData = ohlcv
        .map((p, i) =>
          indicators.macd!.dif[i] != null
            ? { time: toUTCTime(p.timestamp), value: indicators.macd!.dif[i] }
            : null
        )
        .filter((d) => d !== null);
      macdSeries.setData(macdData);

      if (indicators.macd.dea?.length) {
        const deaSeries = chart.addLineSeries({
          color: "#f59e0b",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          priceScaleId: "macd",
        });
        const deaData = ohlcv
          .map((p, i) =>
            indicators.macd!.dea![i] != null
              ? { time: toUTCTime(p.timestamp), value: indicators.macd!.dea![i] }
              : null
          )
          .filter((d) => d !== null);
        deaSeries.setData(deaData);
      }
    } else if (indicators.rsi?.length) {
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

      const rsiData = ohlcv
        .map((p, i) =>
          indicators.rsi![i] != null
            ? { time: toUTCTime(p.timestamp), value: indicators.rsi![i] }
            : null
        )
        .filter((d) => d !== null);
      rsiSeries.setData(rsiData);
    }

    chart.timeScale().fitContent();

    // Auto-resize
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        chart.applyOptions({ width });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [ohlcv, indicators, trades]);

  if (!ohlcv.length) {
    return (
      <div className="h-40 bg-bg2 rounded-lg animate-pulse flex items-center justify-center text-xs text-text3">
        暂无 K 线数据
      </div>
    );
  }

  return (
    <div className="w-full">
      {/* Control bar: timeframe + legend */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-2 py-1 text-[10px]">
        {/* Timeframe switcher */}
        <div className="flex gap-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => {
                setActiveTf(tf);
                onTimeframeChange?.(tf);
              }}
              className={`px-2 py-0.5 font-medium rounded cursor-pointer transition-all border ${
                activeTf === tf
                  ? "text-green bg-green-dim border-green/20"
                  : "text-text3 border-transparent hover:text-text hover:bg-bg2"
              }`}
            >
              {tf}
            </button>
          ))}
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
      </div>

      {/* Chart */}
      <div ref={containerRef} className="w-full" />
    </div>
  );
}
