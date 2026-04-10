"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

interface AgentInfo {
  id: string;
  name: string;
  status: string;
  color: string;
  isRunning: boolean;
}

const AGENTS: AgentInfo[] = [
  { id: "orchestrator", name: "Orchestrator", status: "主控", color: "var(--color-purple)", isRunning: true },
  { id: "market", name: "市场分析", status: "运行", color: "var(--color-teal)", isRunning: true },
  { id: "sentiment", name: "情绪分析", status: "运行", color: "var(--color-blue)", isRunning: true },
  { id: "risk", name: "风险管理", status: "告警", color: "var(--color-amber)", isRunning: true },
  { id: "strategy", name: "策略优化", status: "运行", color: "var(--color-green)", isRunning: false },
  { id: "execution", name: "交易执行", status: "就绪", color: "var(--color-green)", isRunning: true },
  { id: "backtest", name: "回测引擎", status: "待机", color: "var(--color-text3)", isRunning: false },
];

const STRATEGIES = [
  { name: "动量突破", meta: "BTC · 15m", active: true, enabled: true, color: "var(--color-green)" },
  { name: "均值回归", meta: "ETH · 1h", active: false, enabled: true, color: "var(--color-blue)" },
  { name: "套利对冲", meta: "多品种", active: false, enabled: false, color: "var(--color-purple)" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const activeAgent = pathname.split("/").pop() === "agents" ? "market" : "orchestrator";
  const [selectedAgent, setSelectedAgent] = useState(activeAgent);
  const [strategies, setStrategies] = useState(STRATEGIES);

  const toggleStrategy = (idx: number) => {
    setStrategies((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, enabled: !s.enabled } : s))
    );
  };

  const getStatusBg = (status: string) => {
    switch (status) {
      case "主控": return "var(--color-purple-dim)";
      case "运行": return "var(--color-green-dim)";
      case "告警": return "var(--color-amber-dim)";
      case "就绪": return "var(--color-green-dim)";
      case "待机": return "var(--color-bg2)";
      default: return "var(--color-bg2)";
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "主控": return "var(--color-purple)";
      case "运行": return "var(--color-teal)";
      case "告警": return "var(--color-amber)";
      case "就绪": return "var(--color-green)";
      case "待机": return "var(--color-text3)";
      default: return "var(--color-text3)";
    }
  };

  return (
    <div className="w-[220px] bg-bg1 border-r border-[rgba(255,255,255,0.07)] flex flex-col overflow-y-auto py-3 px-2">
      {/* Agent cluster */}
      <div className="mb-1.5">
        <div className="text-[9px] font-semibold text-text3 uppercase tracking-widest px-2 pt-1.5 pb-1">
          智能体集群
        </div>
        {AGENTS.map((agent) => (
          <div
            key={agent.id}
            onClick={() => setSelectedAgent(agent.id)}
            className={`flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-all border border-transparent mb-0.5 ${
              agent.id === selectedAgent
                ? "bg-bg3 border-[rgba(255,255,255,0.12)]"
                : "hover:bg-bg2"
            }`}
          >
            <div
              className={`w-[7px] h-[7px] rounded-full flex-shrink-0 ${agent.isRunning ? "animate-pulse-slow" : ""}`}
              style={{ background: agent.color, boxShadow: `0 0 6px ${agent.color}` }}
            />
            <span className="text-xs font-medium text-text flex-1">{agent.name}</span>
            <span
              className="font-mono text-[9px] px-1.5 py-0.5 rounded font-semibold"
              style={{ background: getStatusBg(agent.status), color: getStatusColor(agent.status) }}
            >
              {agent.status}
            </span>
          </div>
        ))}
      </div>

      <div className="h-px bg-[rgba(255,255,255,0.07)] my-2.5 mx-2" />

      {/* Account overview */}
      <div className="mb-1.5">
        <div className="text-[9px] font-semibold text-text3 uppercase tracking-widest px-2 pt-1.5 pb-1">
          账户概况
        </div>
        <div className="px-2.5 py-1.5 flex justify-between items-center">
          <span className="text-xs text-text2">总资产</span>
          <span className="font-mono text-xs font-semibold text-green">$284,720</span>
        </div>
        <div className="px-2.5 py-1.5 flex justify-between items-center">
          <span className="text-xs text-text2">今日盈亏</span>
          <span className="font-mono text-xs font-semibold text-green">+$3,421</span>
        </div>
        <div className="px-2.5 py-1.5 flex justify-between items-center">
          <span className="text-xs text-text2">持仓数</span>
          <span className="font-mono text-xs font-semibold">4</span>
        </div>
        <div className="px-2.5 py-1.5 flex justify-between items-center">
          <span className="text-xs text-text2">信号队列</span>
          <span className="font-mono text-xs font-semibold text-amber">7</span>
        </div>
        <div className="px-2.5 py-1.5 flex justify-between items-center">
          <span className="text-xs text-text2">执行任务</span>
          <span className="font-mono text-xs font-semibold">12</span>
        </div>
      </div>

      <div className="h-px bg-[rgba(255,255,255,0.07)] my-2.5 mx-2" />

      {/* Active strategies */}
      <div className="mb-1.5">
        <div className="text-[9px] font-semibold text-text3 uppercase tracking-widest px-2 pt-1.5 pb-1">
          活跃策略
        </div>
        <div className="px-0.5">
          {strategies.map((strat, idx) => (
            <div
              key={idx}
              className={`px-2.5 py-2 rounded-md border mb-1.5 cursor-pointer transition-all flex items-center gap-2.5 ${
                strat.active
                  ? "border-green/40 bg-green-dim"
                  : "border-[rgba(255,255,255,0.07)] hover:border-[rgba(255,255,255,0.12)] hover:bg-bg2"
              }`}
            >
              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: strat.color }} />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold text-text">{strat.name}</div>
                <div className="text-[10px] text-text3">{strat.meta}</div>
              </div>
              <label className="relative w-8 h-[18px] flex-shrink-0">
                <input
                  type="checkbox"
                  className="hidden"
                  checked={strat.enabled}
                  onChange={() => toggleStrategy(idx)}
                />
                <div
                  className={`absolute inset-0 rounded-[9px] cursor-pointer transition-all before:content-[''] before:absolute before:w-3 before:h-3 before:left-0.5 before:top-0.5 before:rounded-full before:bg-text3 before:transition-all before:duration-200 ${
                    strat.enabled
                      ? "!bg-green-dim !border-green before:!translate-x-[14px] before:!bg-green"
                      : "bg-bg border border-[rgba(255,255,255,0.12)]"
                  }`}
                  style={strat.enabled ? { borderColor: "var(--color-green)" } : {}}
                />
              </label>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
