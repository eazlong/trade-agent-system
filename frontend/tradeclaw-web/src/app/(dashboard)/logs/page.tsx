"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useLogs } from "@/hooks/useLogs";
import type { SystemLog } from "@/lib/api";

// Map backend module to display name and color
const MODULE_DISPLAY: Record<string, { label: string; color: string }> = {
  agent: { label: "Orchestrator", color: "var(--color-purple)" },
  trading: { label: "交易执行", color: "var(--color-green)" },
  riskguard: { label: "风险管理", color: "var(--color-amber)" },
  signal_monitor: { label: "信号监控", color: "var(--color-teal)" },
  memory: { label: "记忆系统", color: "var(--color-blue)" },
  channel: { label: "通道", color: "var(--color-teal)" },
  notify: { label: "通知", color: "var(--color-blue)" },
  exchange: { label: "交易所", color: "var(--color-green)" },
  datasource: { label: "数据源", color: "var(--color-teal)" },
  auth: { label: "认证", color: "var(--color-purple)" },
  backtest: { label: "回测引擎", color: "var(--color-text3)" },
  skill: { label: "技能", color: "var(--color-teal)" },
  core: { label: "系统核心", color: "var(--color-purple)" },
  unknown: { label: "未知", color: "var(--color-text3)" },
};

// Map backend levels to display
const LEVEL_DISPLAY: Record<string, string> = {
  DEBUG: "DEBUG",
  INFO: "INFO",
  WARNING: "WARN",
  ERROR: "ERROR",
  CRITICAL: "ERROR",
};

const LEVEL_COLORS: Record<string, string> = {
  INFO: "text-green",
  WARN: "text-amber",
  ERROR: "text-red",
  DEBUG: "text-text3",
};

const LEVEL_BG: Record<string, string> = {
  INFO: "bg-green-dim",
  WARN: "bg-amber-dim",
  ERROR: "bg-red-dim",
  DEBUG: "bg-bg2",
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toTimeString().slice(0, 12);
}

function getModuleInfo(module: string) {
  return MODULE_DISPLAY[module] || MODULE_DISPLAY.unknown;
}

export default function LogsPage() {
  const [filter, setFilter] = useState<string>("ALL");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Map frontend filter to backend level param
  const apiLevel = useMemo(() => {
    if (filter === "ALL") return undefined;
    if (filter === "WARN") return "WARNING";
    if (filter === "ERROR") return "ERROR";
    return filter;
  }, [filter]);

  const { logs, loading, hasNext, loadingMore, loadMore, wsConnected, wsError } = useLogs({
    level: apiLevel,
    search: searchQuery || undefined,
  }, autoRefresh);

  // Intersection observer for infinite scroll
  const handleObserver = useCallback((entries: IntersectionObserverEntry[]) => {
    const [entry] = entries;
    if (entry.isIntersecting && hasNext && !loadingMore) {
      loadMore();
    }
  }, [hasNext, loadingMore, loadMore]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(handleObserver, { root: sentinel.parentElement, rootMargin: "200px" });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [handleObserver]);

  // Count levels from current logs for badge display
  const levelCounts = useMemo(() => {
    const counts: Record<string, number> = { ALL: 0, INFO: 0, WARN: 0, ERROR: 0, DEBUG: 0 };
    for (const log of logs) {
      const displayLevel = LEVEL_DISPLAY[log.level] || "INFO";
      counts.ALL++;
      if (displayLevel in counts) counts[displayLevel]++;
    }
    return counts;
  }, [logs]);

  const filteredLogs = useMemo(() => {
    // Already filtered by API, just do client-side level mapping for display
    return logs.map((log) => ({
      ...log,
      displayLevel: LEVEL_DISPLAY[log.level] || "INFO",
      moduleInfo: getModuleInfo(log.module),
      displayTime: formatTime(log.created_at),
    }));
  }, [logs]);

  return (
    <DashboardShell>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <h1 className="text-sm font-bold">日志中心</h1>
          <span className="text-xs text-text3">·</span>
          <span className="text-xs text-text3 font-mono">{levelCounts.ALL} 条日志</span>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="搜索日志..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-1.5 text-xs text-text outline-none focus:border-green w-48"
          />
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold border cursor-pointer transition-all flex items-center gap-1.5 ${
              autoRefresh
                ? "bg-green-dim border-green/20 text-green"
                : "bg-bg2 border-[rgba(255,255,255,0.07)] text-text3"
            }`}
          >
            {autoRefresh ? (
              <>
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-green" : wsError ? "bg-red animate-pulse" : "bg-amber animate-pulse"}`} />
                实时
              </>
            ) : (
              "已暂停"
            )}
          </button>
        </div>
      </div>

      {/* Filter tabs */}
      {wsError && autoRefresh && (
        <div className="px-3 py-2 bg-red-dim/30 border border-red/20 rounded-lg text-xs text-red flex items-center gap-2">
          <span className="font-semibold">实时连接异常:</span>
          <span className="text-text2">{wsError}</span>
          <button
            onClick={() => {
              setAutoRefresh(false);
              setTimeout(() => setAutoRefresh(true), 100);
            }}
            className="ml-auto px-2 py-0.5 bg-red/20 rounded hover:bg-red/30 transition-colors"
          >
            重连
          </button>
        </div>
      )}
      <div className="flex gap-1.5">
        {(["ALL", "INFO", "WARN", "ERROR", "DEBUG"] as const).map((level) => (
          <button
            key={level}
            onClick={() => setFilter(level)}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold cursor-pointer transition-all border ${
              filter === level
                ? level === "WARN"
                  ? "bg-amber-dim border-amber/20 text-amber"
                  : level === "ERROR"
                  ? "bg-red-dim border-red/20 text-red"
                  : "bg-green-dim border-green/20 text-green"
                : "bg-bg2 border-[rgba(255,255,255,0.07)] text-text3 hover:text-text"
            }`}
          >
            {level === "ALL" ? "全部" : level} ({levelCounts[level]})
          </button>
        ))}
      </div>

      {/* Log list */}
      <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
        {/* Header */}
        <div className="px-4 py-2.5 border-b border-[rgba(255,255,255,0.07)] flex items-center gap-4 text-[9px] text-text3 uppercase tracking-wider font-semibold">
          <span className="w-24">时间</span>
          <span className="w-14">级别</span>
          <span className="w-24">模块</span>
          <span className="flex-1">消息</span>
        </div>

        {/* Rows */}
        <div className="max-h-[calc(100vh-260px)] overflow-y-auto font-mono text-xs">
          {loading ? (
            <div className="px-4 py-8 text-center text-text3 text-xs">加载中...</div>
          ) : filteredLogs.length === 0 ? (
            <div className="px-4 py-8 text-center text-text3 text-xs">暂无日志</div>
          ) : (
            <>
              {filteredLogs.map((log) => (
                <div
                  key={log.id}
                  className="px-4 py-1.5 border-b border-[rgba(255,255,255,0.03)] hover:bg-bg2/50 transition-colors flex items-center gap-4"
                >
                  <span className="w-24 text-text3 flex-shrink-0">{log.displayTime}</span>
                  <span className={`w-14 flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded text-center ${LEVEL_BG[log.displayLevel]} ${LEVEL_COLORS[log.displayLevel]}`}>
                    {log.displayLevel}
                  </span>
                  <span className="w-24 flex-shrink-0" style={{ color: log.moduleInfo.color }}>
                    {log.moduleInfo.label}
                  </span>
                  <span className="text-text2 flex-1">{log.message}</span>
                </div>
              ))}
              {/* Sentinel for infinite scroll */}
              <div ref={sentinelRef} className="py-4 text-center text-text3 text-xs">
                {loadingMore ? "加载更多..." : hasNext ? "" : ""}
              </div>
            </>
          )}
        </div>
      </div>
    </DashboardShell>
  );
}
