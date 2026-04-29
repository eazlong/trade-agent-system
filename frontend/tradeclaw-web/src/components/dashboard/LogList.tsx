"use client";

import { useState, useEffect, useCallback } from "react";
import { loggingApi, SystemLog } from "@/lib/api";

const LEVEL_COLOR_MAP: Record<string, string> = {
  DEBUG: "var(--color-text3)",
  INFO: "var(--color-teal)",
  WARNING: "var(--color-amber)",
  ERROR: "var(--color-red)",
  CRITICAL: "var(--color-red)",
};

function formatTime(isoStr: string): string {
  const d = new Date(isoStr);
  return d.toTimeString().slice(0, 8);
}

function highlightText(text: string) {
  return text.split(/(HIGH|LOW|\+[\d.]+%|-[\d.]+%|[\d.]+%|[\d.]+)/g).map((part, i) => {
    if (/^(HIGH|LOW|\+[\d.]+%|-[\d.]+%|[\d.]+%|[\d.]+)$/.test(part)) {
      return <span key={i} className="text-text font-semibold">{part}</span>;
    }
    return part;
  });
}

export default function LogList() {
  const [logs, setLogs] = useState<SystemLog[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchLogs = useCallback(() => {
    loggingApi
      .getLogs({ page_size: 20 })
      .then((res) => setLogs(res.results))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  if (error) {
    return <div className="font-mono text-xs text-red py-2">加载失败: {error}</div>;
  }

  if (logs.length === 0) {
    return (
      <div className="font-mono text-xs flex flex-col gap-0.5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex gap-2.5 py-0.5 animate-pulse">
            <div className="h-3.5 bg-bg rounded w-14" />
            <div className="h-3.5 bg-bg rounded w-20" />
            <div className="h-3.5 bg-bg rounded flex-1" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="font-mono text-xs flex flex-col gap-0.5">
      {logs.map((log) => {
        const color = LEVEL_COLOR_MAP[log.level] || "var(--color-text3)";
        return (
          <div key={log.id} className="flex gap-2.5 py-0.5">
            <span className="text-text3 flex-shrink-0">{formatTime(log.created_at)}</span>
            <span className="flex-shrink-0 min-w-[100px]" style={{ color }}>
              {log.module}
            </span>
            <span className="text-text2">{highlightText(log.message)}</span>
          </div>
        );
      })}
    </div>
  );
}
