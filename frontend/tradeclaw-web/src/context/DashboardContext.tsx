"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { agentApi, AgentInfo, tradingApi, TradingSummary } from "@/lib/api";

interface DashboardState {
  agents: AgentInfo[];
  summary: TradingSummary | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

const DashboardContext = createContext<DashboardState | null>(null);

const POLL_INTERVAL = 30_000;

export function DashboardProvider({ children }: { children: React.ReactNode }) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [summary, setSummary] = useState<TradingSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [agentsRes, summaryRes] = await Promise.all([
        agentApi.listAgents(),
        tradingApi.getSummary().catch(() => null),
      ]);
      setAgents(agentsRes.agents);
      setSummary(summaryRes);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const timer = setInterval(fetchAll, POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [fetchAll]);

  return (
    <DashboardContext.Provider value={{ agents, summary, loading, error, refresh: fetchAll }}>
      {children}
    </DashboardContext.Provider>
  );
}

export function useDashboard(): DashboardState {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error("useDashboard must be used within DashboardProvider");
  return ctx;
}
