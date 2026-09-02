"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useDashboard } from "@/context/DashboardContext";

const AGENT_ROUTE_MAP: Record<string, string> = {
  supervisor: "/agents",
  analyst: "/agents",
  coach: "/agents",
  quant: "/backtest",
  risk_advisor: "/overview",
  trading_frame: "/trading",
  assist_frame: "/logs",
};

function mapStatus(status: string): string {
  switch (status) {
    case "ready": return "就绪";
    case "standby": return "待命";
    case "running": return "运行中";
    case "stopped": return "已停止";
    default: return status;
  }
}

function getStatusBg(status: string) {
  switch (status) {
    case "就绪": return "var(--color-purple-dim)";
    case "运行中": case "就绪": return "var(--color-green-dim)";
    case "告警": return "var(--color-amber-dim)";
    case "待命": case "已停止": return "var(--color-bg2)";
    default: return "var(--color-bg2)";
  }
}

function getStatusColor(status: string) {
  switch (status) {
    case "就绪": return "var(--color-green)";
    case "运行中": return "var(--color-teal)";
    case "待命": return "var(--color-text3)";
    case "已停止": return "var(--color-text3)";
    default: return "var(--color-text3)";
  }
}

function AgentSkeleton() {
  return (
    <div className="px-2.5 py-2 rounded-lg mb-0.5 animate-pulse">
      <div className="flex items-center gap-2">
        <div className="w-[7px] h-[7px] rounded-full bg-bg2 flex-shrink-0" />
        <div className="h-3 w-16 rounded bg-bg2" />
        <div className="h-3 w-8 rounded bg-bg2 ml-auto" />
      </div>
    </div>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { agents, summary, loading, error } = useDashboard();
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  const handleAgentClick = (agentName: string) => {
    setSelectedAgent(agentName);
    const route = AGENT_ROUTE_MAP[agentName] ?? "/agents";
    router.push(route);
  };

  return (
    <div className="w-[220px] bg-bg1 border-r border-[rgba(255,255,255,0.07)] flex flex-col overflow-y-auto py-3 px-2">
      {/* Agent cluster */}
      <div className="mb-1.5">
        <div className="text-[9px] font-semibold text-text3 uppercase tracking-widest px-2 pt-1.5 pb-1">
          智能体集群
        </div>
        {loading ? (
          Array.from({ length: 7 }).map((_, i) => <AgentSkeleton key={i} />)
        ) : error ? (
          <div className="px-2.5 py-2 text-[10px] text-red">
            加载失败: {error}
          </div>
        ) : (
          agents.map((agent) => {
            const cnStatus = mapStatus(agent.status);
            const tagColor = agent.tag_color;
            const tagBg = agent.tag_bg;
            const isRunning = agent.status === "running" || agent.status === "ready";

            return (
              <div
                key={agent.name}
                onClick={() => handleAgentClick(agent.name)}
                className={`flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-all border border-transparent mb-0.5 ${
                  agent.name === selectedAgent
                    ? "bg-bg3 border-[rgba(255,255,255,0.12)]"
                    : "hover:bg-bg2"
                }`}
              >
                <div
                  className={`w-[7px] h-[7px] rounded-full flex-shrink-0 ${isRunning ? "animate-pulse-slow" : ""}`}
                  style={{ background: tagColor, boxShadow: `0 0 6px ${tagColor}` }}
                />
                <span className="text-xs font-medium text-text flex-1 truncate" title={agent.display_name}>
                  {agent.display_name}
                </span>
                <span
                  className="font-mono text-[9px] px-1.5 py-0.5 rounded font-semibold whitespace-nowrap"
                  style={{ background: tagBg, color: tagColor }}
                >
                  {cnStatus}
                </span>
              </div>
            );
          })
        )}
      </div>

      <div className="h-px bg-[rgba(255,255,255,0.07)] my-2.5 mx-2" />

      {/* Workflow history */}
      <Link
        href="/workflows"
        className={`flex items-center gap-2 px-2.5 py-2 rounded-lg transition-all border border-transparent mb-0.5 ${
          pathname === "/workflows"
            ? "bg-bg3 border-[rgba(255,255,255,0.12)]"
            : "hover:bg-bg2"
        }`}
      >
        <div
          className="w-[7px] h-[7px] rounded-full flex-shrink-0"
          style={{
            background: "var(--color-blue)",
            boxShadow: "0 0 6px var(--color-blue)",
          }}
        />
        <span className="text-xs font-medium text-text flex-1 truncate">
          工作流
        </span>
        <span
          className="font-mono text-[9px] px-1.5 py-0.5 rounded font-semibold whitespace-nowrap"
          style={{
            background: "var(--color-blue-dim)",
            color: "var(--color-blue)",
          }}
        >
          历史
        </span>
      </Link>

      <div className="h-px bg-[rgba(255,255,255,0.07)] my-2.5 mx-2" />

      {/* Account overview */}
      <div className="mb-1.5">
        <div className="text-[9px] font-semibold text-text3 uppercase tracking-widest px-2 pt-1.5 pb-1">
          账户概况
        </div>
        {summary ? (
          <>
            <div className="px-2.5 py-1.5 flex justify-between items-center">
              <span className="text-xs text-text2">总资产</span>
              <span className="font-mono text-xs font-semibold text-green">${summary.total_equity}</span>
            </div>
            <div className="px-2.5 py-1.5 flex justify-between items-center">
              <span className="text-xs text-text2">今日盈亏</span>
              <span className={`font-mono text-xs font-semibold ${Number(summary.today_realized_pnl) >= 0 ? "text-green" : "text-red"}`}>
                {Number(summary.today_realized_pnl) >= 0 ? "+" : ""}${summary.today_realized_pnl}
              </span>
            </div>
            <div className="px-2.5 py-1.5 flex justify-between items-center">
              <span className="text-xs text-text2">活跃订单</span>
              <span className="font-mono text-xs font-semibold">{summary.active_orders_count}</span>
            </div>
            <div className="px-2.5 py-1.5 flex justify-between items-center">
              <span className="text-xs text-text2">今日订单</span>
              <span className="font-mono text-xs font-semibold">{summary.total_orders_today}</span>
            </div>
            <div className="px-2.5 py-1.5 flex justify-between items-center">
              <span className="text-xs text-text2">账户数</span>
              <span className="font-mono text-xs font-semibold">{summary.account_count}</span>
            </div>
          </>
        ) : (
          <div className="px-2.5 py-1.5 text-[10px] text-text3">暂无数据</div>
        )}
      </div>

      <div className="h-px bg-[rgba(255,255,255,0.07)] my-2.5 mx-2" />

      {/* Active strategies - still hardcoded for now */}
      <div className="mb-1.5">
        <div className="text-[9px] font-semibold text-text3 uppercase tracking-widest px-2 pt-1.5 pb-1">
          活跃策略
        </div>
        <div className="px-0.5">
          <div className="px-2.5 py-3 text-[10px] text-text3 text-center">
            即将上线
          </div>
        </div>
      </div>
    </div>
  );
}
