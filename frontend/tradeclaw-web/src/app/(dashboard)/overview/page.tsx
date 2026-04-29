"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import Metrics from "@/components/dashboard/Metrics";
import PnLChart from "@/components/dashboard/PnLChart";
import AgentCards from "@/components/dashboard/AgentCards";
import SignalList from "@/components/dashboard/SignalList";
import LogList from "@/components/dashboard/LogList";

export default function OverviewPage() {

  return (
    <DashboardShell>
      <Metrics />

      {/* Chart + Agent Cards */}
      <div className="grid grid-cols-2 gap-3">
        {/* PnL Chart Card */}
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
            <div className="text-xs font-semibold flex items-center gap-2">
              <svg width="12" height="12" viewBox="0 0 12 12">
                <polyline points="1,9 4,5 7,7 11,2" fill="none" stroke="var(--color-green)" strokeWidth="1.5" strokeLinejoin="round" />
              </svg>
              策略净值曲线
            </div>
            <div className="flex gap-1.5">
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-green-dim text-green">实盘</span>
              <select className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded px-2 py-0.5 text-xs text-text outline-none cursor-pointer">
                <option>近30天</option>
                <option>近90天</option>
                <option>今年</option>
              </select>
            </div>
          </div>
          <div className="px-4 py-3">
            <PnLChart />
          </div>
        </div>

        {/* Agent Status Card */}
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
            <div className="text-xs font-semibold flex items-center gap-2">
              <svg width="12" height="12" viewBox="0 0 12 12">
                <circle cx="6" cy="6" r="5" fill="none" stroke="var(--color-blue)" strokeWidth="1.5" />
                <line x1="6" y1="3" x2="6" y2="6" stroke="var(--color-blue)" strokeWidth="1.5" strokeLinecap="round" />
                <circle cx="6" cy="8.5" r="0.8" fill="var(--color-blue)" />
              </svg>
              智能体实时状态
            </div>
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-purple-dim border border-purple/20 rounded-md text-xs text-purple">
              <span>Orchestrator 规划中</span>
              <span className="animate-blink-dots">.</span>
              <span className="animate-blink-dots" style={{ animationDelay: "0.2s" }}>.</span>
              <span className="animate-blink-dots" style={{ animationDelay: "0.4s" }}>.</span>
            </div>
          </div>
          <AgentCards />
        </div>
      </div>

      {/* Signals + Logs — stretches to fill remaining height */}
      <div className="grid grid-cols-2 gap-3 flex-1 min-h-0">
        {/* Signal Queue */}
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
            <div className="text-xs font-semibold flex items-center gap-2">
              <svg width="12" height="12" viewBox="0 0 12 12">
                <path d="M6 1L7.5 4.5H11L8 7L9.5 11L6 8.5L2.5 11L4 7L1 4.5H4.5Z" fill="var(--color-amber)" />
              </svg>
              实时信号队列
            </div>
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-amber-dim text-amber">
              7 待处理
            </span>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto">
            <SignalList />
          </div>
        </div>

        {/* Log */}
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between">
            <div className="text-xs font-semibold flex items-center gap-2">
              <svg width="12" height="12" viewBox="0 0 12 12">
                <rect x="1" y="1" width="10" height="10" rx="2" fill="none" stroke="var(--color-text3)" strokeWidth="1" />
                <line x1="3" y1="4" x2="9" y2="4" stroke="var(--color-text3)" strokeWidth="1" />
                <line x1="3" y1="6.5" x2="7" y2="6.5" stroke="var(--color-text3)" strokeWidth="1" />
              </svg>
              智能体通信日志
            </div>
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-bg2 text-text3">
              实时
            </span>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2.5">
            <LogList />
          </div>
        </div>
      </div>
    </DashboardShell>
  );
}
