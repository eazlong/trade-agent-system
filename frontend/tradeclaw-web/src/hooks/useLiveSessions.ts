"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  liveSessionApi,
  type LiveSession,
  type CreateSessionPayload,
} from "@/lib/api";

export type SessionAction = "start" | "pause" | "resume" | "stop" | "promote" | "delete";

export function useLiveSessions(pollingInterval = 10000) {
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval>>();

  const fetchSessions = useCallback(async () => {
    try {
      const data = await liveSessionApi.list();
      setSessions(data);
      setError(null);
    } catch {
      // Ignore errors — API may not be available yet
    } finally {
      setLoading(false);
    }
  }, []);

  const doAction = useCallback(
    async (id: string, action: SessionAction) => {
      setActionLoading(true);
      setError(null);
      try {
        switch (action) {
          case "start":
            await liveSessionApi.start(id);
            break;
          case "pause":
            await liveSessionApi.pause(id);
            break;
          case "resume":
            await liveSessionApi.resume(id);
            break;
          case "stop":
            await liveSessionApi.stop(id);
            break;
          case "promote":
            await liveSessionApi.promote(id);
            break;
          case "delete":
            await liveSessionApi.delete(id);
            break;
        }
        // Refresh after action
        await fetchSessions();
      } catch (e) {
        setError(e instanceof Error ? e.message : "操作失败");
      } finally {
        setActionLoading(false);
      }
    },
    [fetchSessions]
  );

  const createSession = useCallback(
    async (data: CreateSessionPayload) => {
      setActionLoading(true);
      setError(null);
      try {
        const result = await liveSessionApi.create(data);
        await fetchSessions();
        return result;
      } catch (e) {
        setError(e instanceof Error ? e.message : "创建失败");
        throw e;
      } finally {
        setActionLoading(false);
      }
    },
    [fetchSessions]
  );

  useEffect(() => {
    fetchSessions();
    pollingRef.current = setInterval(fetchSessions, pollingInterval);
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, [fetchSessions, pollingInterval]);

  return {
    sessions,
    loading,
    actionLoading,
    error,
    refetch: fetchSessions,
    start: (id: string) => void doAction(id, "start"),
    pause: (id: string) => void doAction(id, "pause"),
    resume: (id: string) => void doAction(id, "resume"),
    stop: (id: string) => void doAction(id, "stop"),
    promote: (id: string) => void doAction(id, "promote"),
    remove: (id: string) => void doAction(id, "delete"),
    create: createSession,
  };
}
