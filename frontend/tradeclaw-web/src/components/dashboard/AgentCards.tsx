"use client";

import { useState, useEffect } from "react";
import { agentApi, AgentInfo } from "@/lib/api";

const STATUS_LABEL_MAP: Record<string, string> = {
  running: "运行",
  ready: "就绪",
  standby: "待机",
  stopped: "停止",
};

const STATUS_COLOR_MAP: Record<string, string> = {
  running: "text-green",
  ready: "text-blue",
  standby: "text-amber",
  stopped: "text-text3",
};

function AgentCard({ agent }: { agent: AgentInfo }) {
  const statusColor = STATUS_COLOR_MAP[agent.status] || "text-text3";
  const tagLabel = STATUS_LABEL_MAP[agent.status] || agent.status;

  return (
    <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg p-3">
      <div className="flex items-start justify-between mb-2.5">
        <div>
          <div className="text-xs font-semibold text-text">{agent.display_name}</div>
          <div className="text-[10px] text-text3 mt-0.5">{agent.role}</div>
        </div>
        <span
          className={`text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider ${statusColor}`}
          style={{ color: agent.tag_color, background: agent.tag_bg }}
        >
          {tagLabel}
        </span>
      </div>
      <div className="text-[10px] text-text3 truncate" title={agent.description}>
        {agent.description}
      </div>
    </div>
  );
}

export default function AgentCards() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    agentApi
      .listAgents()
      .then((res) => setAgents(res.agents))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return <div className="p-4 text-red text-xs">加载失败: {error}</div>;
  }

  if (agents.length === 0) {
    return (
      <div className="grid grid-cols-2 gap-2 p-2.5">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg p-3 animate-pulse"
          >
            <div className="flex items-start justify-between mb-2.5">
              <div>
                <div className="h-3.5 bg-bg rounded w-16 mb-1" />
                <div className="h-2.5 bg-bg rounded w-24" />
              </div>
            </div>
            <div className="h-2.5 bg-bg rounded w-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-2 p-2.5">
      {agents.map((agent) => (
        <AgentCard key={agent.name} agent={agent} />
      ))}
    </div>
  );
}
