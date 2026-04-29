"use client";

import { useState, useEffect, useCallback } from "react";
import { loggingApi, type SystemLog, type LogQueryParams } from "@/lib/api";
import { logWS } from "@/lib/websocket";

export function useLogs(params?: LogQueryParams, live = true) {
  const [logs, setLogs] = useState<SystemLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasNext, setHasNext] = useState(false);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [wsError, setWsError] = useState<string | null>(null);
  const PAGE_SIZE = 200;
  const paramsKey = JSON.stringify(params);

  // Load initial history via REST — re-fetches when params change
  const fetchLogs = useCallback(async (p = 1, append = false) => {
    try {
      if (!append) setLoading(true);
      else setLoadingMore(true);
      const data = await loggingApi.getLogs({
        ...params,
        page: p,
        page_size: PAGE_SIZE,
      });
      if (append) {
        setLogs((prev) => [...prev, ...data.results]);
      } else {
        setLogs(data.results);
      }
      setHasNext(data.has_next);
      setPage(data.page);
      setError(null);
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [paramsKey]);

  // Reset page and refetch when params change
  useEffect(() => {
    setLogs([]);
    setPage(1);
    setHasNext(false);
    fetchLogs(1, false);
  }, [paramsKey]);

  const loadMore = useCallback(() => {
    if (!hasNext || loadingMore) return;
    const nextPage = page + 1;
    setPage(nextPage);
    fetchLogs(nextPage, true);
  }, [hasNext, loadingMore, page, fetchLogs]);

  // WebSocket real-time updates
  useEffect(() => {
    if (!live) return;

    logWS.connect();

    // Poll WebSocket connection status
    const statusInterval = setInterval(() => {
      setWsConnected(logWS.isConnected);
    }, 1000);

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
        setWsError(null);
      } else if (msg.type === "error") {
        setWsError(msg.error);
      } else if (msg.type === "subscribed") {
        setWsError(null);
      }
    });

    return () => {
      unsub();
      clearInterval(statusInterval);
    };
  }, [live, paramsKey]);

  return { logs, loading, error, hasNext, loadingMore, loadMore, refetch: () => fetchLogs(1, false), wsConnected, wsError };
}
