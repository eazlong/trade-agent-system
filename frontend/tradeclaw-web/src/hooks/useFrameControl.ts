"use client";

import { useState, useEffect, useCallback } from "react";
import { agentApi } from "@/lib/api";

export type FrameAction = "start" | "stop";

export interface FrameStatus {
  trading: "stopped" | "starting" | "running" | "stopping";
  assist: "stopped" | "starting" | "running" | "stopping";
  risk_guard: "running" | "stopped";
  order_executor: "running" | "stopped";
  data_feed: "running" | "stopped";
}

const DEFAULT_STATUS: FrameStatus = {
  trading: "stopped",
  assist: "stopped",
  risk_guard: "stopped",
  order_executor: "stopped",
  data_feed: "stopped",
};

export function useFrameControl(pollingInterval = 5000) {
  const [status, setStatus] = useState<FrameStatus>(DEFAULT_STATUS);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await agentApi.getFrameStatus();
      setStatus(data as unknown as FrameStatus);
      setError(null);
    } catch {
      // Ignore errors — frame status endpoint may not always be reachable
    } finally {
      setLoading(false);
    }
  }, []);

  const doAction = useCallback(async (action: FrameAction) => {
    setActionLoading(true);
    setError(null);
    try {
      await agentApi.controlFrame(action);
      // The backend starts the frame synchronously.
      // Poll for status updates — fetchStatus updates the React state
      // directly so the component re-renders correctly.
      for (let i = 0; i < 5; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const data = await agentApi.getFrameStatus();
          const s = data as unknown as FrameStatus;
          setStatus(s);
          if (s.order_executor === "running" || s.trading === "running") {
            break;
          }
        } catch {
          // continue polling
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActionLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, pollingInterval);
    return () => clearInterval(timer);
  }, [fetchStatus, pollingInterval]);

  return {
    status,
    loading,
    actionLoading,
    error,
    refetch: fetchStatus,
    start: () => void doAction("start"),
    stop: () => void doAction("stop"),
  };
}
