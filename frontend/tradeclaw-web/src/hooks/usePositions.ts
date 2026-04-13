"use client";

import { useState, useEffect } from "react";
import { tradingApi, type PositionResponse } from "@/lib/api";

export function usePositions(pollingInterval = 10000) {
  const [positions, setPositions] = useState<PositionResponse>({
    positions: [],
    executor_running: false,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPositions = async () => {
    try {
      const data = await tradingApi.getPositions();
      setPositions(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPositions();
    const timer = setInterval(fetchPositions, pollingInterval);
    return () => clearInterval(timer);
  }, [pollingInterval]);

  return { positions, loading, error, refetch: fetchPositions };
}
