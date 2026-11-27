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
      // 这里刻意不发 unsubscribe：改筛选条件时本 effect 会重跑，而此时
      // 回调已删、监听数为 0，判定必然成立 —— 每敲一个字符就会让服务端
      // 停掉 Redis 读流再重建一个客户端，中间产生的日志直接丢。
      // 重跑前总会有一次新的 subscribe（服务端订阅路径内部会关掉旧池），
      // 真正的取订与断连统一交给下面的卸载 effect。
    };
  }, [live, paramsKey]);

  // 组件真的卸载时才取订并断开连接。原先没有任何地方调 disconnect：每进出
  // 一次日志页，后端就多留一条常驻 WS 连接，挂机久了把 nofile 吃满。
  useEffect(() => {
    return () => {
      // 还有其他订阅者（多实例共存）就不断连，避免拖断别人的流
      if (logWS.listenerCount === 0) {
        logWS.unsubscribe();
        logWS.disconnect();
      }
    };
  }, []);

  return { logs, loading, error, hasNext, loadingMore, loadMore, refetch: () => fetchLogs(1, false), wsConnected, wsError };
}
