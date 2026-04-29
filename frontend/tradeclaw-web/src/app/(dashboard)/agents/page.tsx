"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import { useState, useEffect } from "react";
import { agentApi, AgentInfo } from "@/lib/api";

interface AgentDetail {
  id: string;
  name: string;
  role: string;
  description: string;
  status: string;
  tagColor: string;
  tagBg: string;
  tools: string[];
  intent: string;
  frameState?: string;
}

function mapAgentInfo(api: AgentInfo): AgentDetail {
  return {
    id: api.name,
    name: api.display_name,
    role: api.role,
    description: api.description,
    status: api.status === "ready" ? "就绪" : api.status === "standby" ? "待命" : api.status === "running" ? "运行中" : "已停止",
    tagColor: api.tag_color,
    tagBg: api.tag_bg,
    tools: api.tools,
    intent: typeof api.intent === "string" ? api.intent : JSON.stringify(api.intent),
    frameState: api.frame_state,
  };
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentDetail[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<AgentDetail | null>(null);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    agentApi.listAgents()
      .then((res) => {
        if (cancelled) return;
        const mapped = res.agents.map(mapAgentInfo);
        setAgents(mapped);
        setSelectedAgent(mapped[0] ?? null);
        setActiveIdx(mapped.length > 0 ? 0 : -1);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "加载失败");
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex items-center gap-2 mb-1">
          <h1 className="text-sm font-bold">智能体管理</h1>
          <span className="text-xs text-text3">·</span>
          <span className="text-xs text-text3">加载中...</span>
        </div>
        <div className="flex items-center justify-center py-12 text-text3 text-sm">
          正在加载智能体信息...
        </div>
      </DashboardShell>
    );
  }

  if (error) {
    return (
      <DashboardShell>
        <div className="flex items-center gap-2 mb-1">
          <h1 className="text-sm font-bold">智能体管理</h1>
          <span className="text-xs text-text3">·</span>
          <span className="text-xs text-text3">加载失败</span>
        </div>
        <div className="flex items-center justify-center py-12 text-red text-sm">
          {error}
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="flex items-center gap-2 mb-1">
        <h1 className="text-sm font-bold">智能体管理</h1>
        <span className="text-xs text-text3">·</span>
        <span className="text-xs text-text3">{agents.length} 个 Agent</span>
      </div>

      {/* Agent detail list */}
      <div className="flex flex-col gap-3">
        {agents.map((agent, idx) => (
          <div
            key={agent.id}
            onClick={() => { setSelectedAgent(agent); setActiveIdx(idx); }}
            className={`bg-bg1 border rounded-xl overflow-hidden cursor-pointer transition-all ${
              activeIdx === idx ? "border-green/30 bg-[rgba(0,230,118,0.02)]" : "border-[rgba(255,255,255,0.07)] hover:border-[rgba(255,255,255,0.12)]"
            }`}
          >
            {/* Header */}
            <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div
                  className={`w-2.5 h-2.5 rounded-full ${agent.status === "运行中" || agent.status === "就绪" ? "animate-pulse-slow" : ""}`}
                  style={{ background: agent.tagColor, boxShadow: `0 0 6px ${agent.tagColor}` }}
                />
                <div>
                  <div className="text-xs font-semibold">{agent.name}</div>
                  <div className="text-[10px] text-text3">{agent.role}</div>
                </div>
              </div>
              <span className="text-[9px] font-bold px-2 py-1 rounded uppercase tracking-wider" style={{ color: agent.tagColor, background: agent.tagBg }}>
                {agent.status}
              </span>
            </div>

            {activeIdx === idx && (
              <div className="p-4">
                <p className="text-xs text-text2 mb-3 whitespace-pre-wrap">{agent.description}</p>

                {/* Tools */}
                {agent.tools.length > 0 && (
                  <div className="mb-3">
                    <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold mb-1.5">可用工具</div>
                    <div className="flex flex-wrap gap-1.5">
                      {agent.tools.map((tool, i) => (
                        <span
                          key={i}
                          className="text-[9px] font-mono px-2 py-0.5 rounded bg-bg2 border border-[rgba(255,255,255,0.07)] text-text2"
                        >
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Intent / Frame info */}
                <div className="font-mono text-xs">
                  <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold mb-1.5">路由信息</div>
                  <div className="text-text2 py-0.5">
                    意图: <span className="text-text">{agent.intent}</span>
                  </div>
                  {agent.frameState && (
                    <div className="text-text2 py-0.5">
                      框架状态: <span className="text-text">{agent.frameState}</span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </DashboardShell>
  );
}
