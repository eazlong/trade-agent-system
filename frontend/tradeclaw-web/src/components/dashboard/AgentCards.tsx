"use client";

import { useState, useEffect } from "react";

interface AgentCardData {
  id: string;
  name: string;
  role: string;
  tagLabel: string;
  tagColor: string;
  tagBg: string;
  metricLabel: string;
  metricValue: number;
  metricColor: string;
  progressColor: string;
}

const AGENTS: AgentCardData[] = [
  { id: "orchestrator", name: "Orchestrator", role: "任务调度 · 信号聚合", tagLabel: "主控", tagColor: "var(--color-purple)", tagBg: "var(--color-purple-dim)", metricLabel: "调度效率", metricValue: 98.2, metricColor: "text-green", progressColor: "var(--color-purple)" },
  { id: "market", name: "市场分析", role: "技术指标 · 形态识别", tagLabel: "运行", tagColor: "var(--color-teal)", tagBg: "var(--color-teal-dim)", metricLabel: "信号置信度", metricValue: 84.7, metricColor: "text-green", progressColor: "var(--color-teal)" },
  { id: "sentiment", name: "情绪分析", role: "NLP · 社交媒体", tagLabel: "运行", tagColor: "var(--color-blue)", tagBg: "var(--color-blue-dim)", metricLabel: "情绪评分", metricValue: 72.3, metricColor: "text-green", progressColor: "var(--color-blue)" },
  { id: "risk", name: "风险管理", role: "VaR · 仓位控制", tagLabel: "告警", tagColor: "var(--color-amber)", tagBg: "var(--color-amber-dim)", metricLabel: "风险水位", metricValue: 63.1, metricColor: "text-amber", progressColor: "var(--color-amber)" },
  { id: "strategy", name: "策略优化", role: "参数调优 · 回测", tagLabel: "运行", tagColor: "var(--color-green)", tagBg: "var(--color-green-dim)", metricLabel: "优化进度", metricValue: 91.5, metricColor: "text-green", progressColor: "var(--color-green)" },
  { id: "execution", name: "交易执行", role: "订单路由 · 滑点", tagLabel: "就绪", tagColor: "var(--color-green)", tagBg: "var(--color-green-dim)", metricLabel: "执行质量", metricValue: 96.8, metricColor: "text-green", progressColor: "var(--color-green)" },
];

export default function AgentCards() {
  const [agents, setAgents] = useState(AGENTS);

  useEffect(() => {
    const timer = setInterval(() => {
      setAgents((prev) =>
        prev.map((a) => {
          const ranges: Record<string, [number, number]> = {
            market: [70, 95],
            sentiment: [55, 85],
            risk: [50, 75],
            strategy: [80, 98],
            execution: [90, 99],
            orchestrator: [95, 99],
          };
          const [min, max] = ranges[a.id] || [50, 99];
          const next = Math.max(min, Math.min(max, a.metricValue + (Math.random() - 0.48) * 3));
          return { ...a, metricValue: parseFloat(next.toFixed(1)) };
        })
      );
    }, 2500);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="grid grid-cols-2 gap-2 p-2.5">
      {agents.map((agent) => (
        <div key={agent.id} className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg p-3">
          <div className="flex items-start justify-between mb-2.5">
            <div>
              <div className="text-xs font-semibold text-text">{agent.name}</div>
              <div className="text-[10px] text-text3 mt-0.5">{agent.role}</div>
            </div>
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider" style={{ color: agent.tagColor, background: agent.tagBg }}>
              {agent.tagLabel}
            </span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-text3">{agent.metricLabel}</span>
            <span className={`font-mono text-xs font-semibold ${agent.metricColor}`}>
              {agent.metricValue.toFixed(1)}%
            </span>
          </div>
          <div className="h-[3px] bg-bg rounded-full overflow-hidden mt-2">
            <div
              className="h-full rounded-full transition-all duration-1000 ease-out"
              style={{ width: `${agent.metricValue}%`, background: agent.progressColor }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
