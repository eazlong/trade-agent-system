"use client";

import { useState, useEffect } from "react";
import { tradingApi, type Order } from "@/lib/api";

export function useOrders(pollingInterval = 10000) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOrders = async () => {
    try {
      const data = await tradingApi.getOrders();
      setOrders(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
    const timer = setInterval(fetchOrders, pollingInterval);
    return () => clearInterval(timer);
  }, [pollingInterval]);

  return { orders, loading, error, refetch: fetchOrders };
}
