"use client";

import { useState, useEffect } from "react";
import { tradingApi, type TradingSummary } from "@/lib/api";

export function useTradingSummary(pollingInterval = 15000) {
  const [summary, setSummary] = useState<TradingSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSummary = async () => {
    try {
      const data = await tradingApi.getSummary();
      setSummary(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummary();
    const timer = setInterval(fetchSummary, pollingInterval);
    return () => clearInterval(timer);
  }, [pollingInterval]);

  return { summary, loading, error, refetch: fetchSummary };
}
