"use client";

import { useState, useEffect } from "react";
import { tradingApi, type Strategy } from "@/lib/api";

export function useStrategies() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStrategies = async () => {
    try {
      const data = await tradingApi.getStrategies();
      setStrategies(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStrategies();
  }, []);

  return { strategies, loading, error, refetch: fetchStrategies };
}
