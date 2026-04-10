"use client";

import { useState, useEffect } from "react";
import { riskApi, type RiskEvent } from "@/lib/api";

export function useRiskEvents(pollingInterval = 15000) {
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchEvents = async () => {
    try {
      const data = await riskApi.getEvents();
      setEvents(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEvents();
    const timer = setInterval(fetchEvents, pollingInterval);
    return () => clearInterval(timer);
  }, [pollingInterval]);

  return { events, loading, error, refetch: fetchEvents };
}
