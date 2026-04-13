"use client";

import { useState, useEffect } from "react";
import { tradingApi, type ExchangeAccountWithBalance } from "@/lib/api";

export function useAccounts(pollingInterval = 20000) {
  const [accounts, setAccounts] = useState<ExchangeAccountWithBalance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAccounts = async () => {
    try {
      const data = await tradingApi.getAccounts();
      setAccounts(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAccounts();
    const timer = setInterval(fetchAccounts, pollingInterval);
    return () => clearInterval(timer);
  }, [pollingInterval]);

  return { accounts, loading, error, refetch: fetchAccounts };
}
