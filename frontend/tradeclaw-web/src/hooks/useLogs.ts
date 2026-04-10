"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { loggingApi, type SystemLog, type LogQueryParams } from "@/lib/api";
import { logWS } from "@/lib/websocket";

export function useLogs(params?: LogQueryParams, live = true) {
  const [logs, setLogs] = useState<SystemLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  // Load initial history via REST
  const fetchLogs = useCallback(async () => {
    try {
      const data = await loggingApi.getLogs(paramsRef.current);
      setLogs(data);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // WebSocket real-time updates
  useEffect(() => {
    if (!live) return;

    logWS.connect();

    logWS.subscribe({
      level: params?.level,
      search: params?.search,
    });

    const unsub = logWS.onMessage((msg) => {
      if (msg.type === "log_entry") {
        const entry: SystemLog = {
          id: msg.id,
          level: msg.level as SystemLog["level"],
          module: msg.module,
          logger_name: "",
          message: msg.message,
          trace_id: msg.trace_id,
          extra_data: msg.extra_data,
          created_at: msg.created_at,
        };
        setLogs((prev) => [entry, ...prev]);
      }
    });

    return () => {
      unsub();
    };
  }, [live, params?.level, params?.search]);

  return { logs, loading, error, refetch: fetchLogs };
}
