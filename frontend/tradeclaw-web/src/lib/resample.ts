import type { OHLCVPoint, IndicatorData } from "@/lib/api";

/** Valid target timeframes for resampling */
export const RESAMPLE_TARGETS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

/** Parse timeframe string to minutes */
export function timeframeToMinutes(tf: string): number {
  const match = tf.match(/^(\d+)([mhd])$/);
  if (!match) return 1;
  const value = parseInt(match[1], 10);
  switch (match[2]) {
    case "m": return value;
    case "h": return value * 60;
    case "d": return value * 1440;
    default: return value;
  }
}

/**
 * Resample OHLCV data from a finer to a coarser timeframe.
 *
 * Aggregation rules:
 *   open  = first open in the group
 *   high  = max high in the group
 *   low   = min low in the group
 *   close = last close in the group
 *   volume = sum of volumes in the group
 *
 * If targetMinutes < baseMinutes, returns the original data unchanged.
 */
export function resampleOHLCV(
  ohlcv: OHLCVPoint[],
  baseTimeframe: string,
  targetTimeframe: string
): OHLCVPoint[] {
  const baseMin = timeframeToMinutes(baseTimeframe);
  const targetMin = timeframeToMinutes(targetTimeframe);

  // Cannot upsample (e.g. 1h → 1m), return as-is
  if (targetMin <= baseMin) return ohlcv;

  if (!ohlcv.length) return [];

  const groupSize = Math.round(targetMin / baseMin);
  const groups: OHLCVPoint[][] = [];

  for (let i = 0; i < ohlcv.length; i += groupSize) {
    groups.push(ohlcv.slice(i, i + groupSize));
  }

  return groups.map((group) => {
    const first = group[0];
    const last = group[group.length - 1];
    return {
      timestamp: first.timestamp,
      open: first.open,
      high: Math.max(...group.map((b) => b.high)),
      low: Math.min(...group.map((b) => b.low)),
      close: last.close,
      volume: group.reduce((sum, b) => sum + b.volume, 0),
    };
  });
}

/**
 * Compute basic indicators from resampled OHLCV data.
 *
 * Since the original strategy indicators are tied to the base timeframe,
 * we compute simple SMAs from the resampled close prices as a visual
 * reference. MACD/RSI/boll are not computed (would need full recalculation).
 */
export function computeIndicators(
  ohlcv: OHLCVPoint[],
  originalIndicators: IndicatorData
): IndicatorData {
  // If resampled to same or finer timeframe, return original indicators
  if (ohlcv.length === 0) return originalIndicators;

  const closes = ohlcv.map((p) => p.close);

  function sma(values: number[], period: number): (number | null)[] {
    return values.map((_, i) => {
      if (i < period - 1) return null;
      const slice = values.slice(i - period + 1, i + 1);
      return slice.reduce((a, b) => a + b, 0) / period;
    });
  }

  const result: IndicatorData = {
    ma7: sma(closes, 7).filter((v): v is number => v !== null),
    ma25: sma(closes, 25).filter((v): v is number => v !== null),
  };

  // Only compute ma99 if we have enough data points
  if (closes.length >= 99) {
    result.ma99 = sma(closes, 99).filter((v): v is number => v !== null);
  }

  return result;
}
