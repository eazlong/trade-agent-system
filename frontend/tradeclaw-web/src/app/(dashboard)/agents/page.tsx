"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import AgentCards from "@/components/dashboard/AgentCards";
import { useState, useEffect } from "react";

interface AgentDetail {
  id: string;
  name: string;
  role: string;
  description: string;
  status: string;
  tagColor: string;
  tagBg: string;
  metrics: { label: string; value: string; color: string }[];
  logs: string[];
}

const AGENT_DETAILS: AgentDetail[] = [
  {
    id: "orchestrator",
    name: "Orchestrator",
    role: "任务调度 · 信号聚合",
    description: "核心编排器，负责任务分配、信号汇总和跨 Agent 通信协调。使用 LLM 进行意图理解和决策规划。",
    status: "运行中",
    tagColor: "var(--color-purple)",
    tagBg: "var(--color-purple-dim)",
    metrics: [
      { label: "调度效率", value: "98.2%", color: "text-green" },
      { label: "任务队列", value: "12", color: "" },
      { label: "平均延迟", value: "45ms", color: "text-green" },
      { label: "今日调度", value: "1,847", color: "" },
    ],
    logs: [
      "→ 市场分析: 触发 BTC 扫描任务",
      "← 风险管理: 接收仓位预警",
      "→ 情绪分析: 请求新闻扫描",
      "← 策略优化: 参数更新完成",
    ],
  },
  {
    id: "market",
    name: "市场分析",
    role: "技术指标 · 形态识别",
    description: "分析 K 线形态、技术指标（RSI/MACD/BOLL 等），识别突破、背离等交易信号。",
    status: "运行中",
    tagColor: "var(--color-teal)",
    tagBg: "var(--color-teal-dim)",
    metrics: [
      { label: "信号置信度", value: "84.7%", color: "text-green" },
      { label: "监控品种", value: "28", color: "" },
      { label: "扫描周期", value: "15s", color: "text-green" },
      { label: "今日信号", value: "342", color: "" },
    ],
    logs: [
      "BTC: RSI 超买预警 → 风险管理",
      "SOL: 突破确认 (置信度 84%)",
      "ETH: 均线死叉预警",
      "AVAX: 量能放大 2.3x",
    ],
  },
  {
    id: "sentiment",
    name: "情绪分析",
    role: "NLP · 社交媒体",
    description: "抓取 Twitter、Reddit、Telegram 等社交媒体数据，通过 NLP 分析市场情绪和趋势。",
    status: "运行中",
    tagColor: "var(--color-blue)",
    tagBg: "var(--color-blue-dim)",
    metrics: [
      { label: "情绪评分", value: "72.3", color: "text-green" },
      { label: "数据源", value: "12", color: "" },
      { label: "分析延迟", value: "2.1s", color: "text-green" },
      { label: "今日分析", value: "8,421", color: "" },
    ],
    logs: [
      "Twitter: 多头情绪 +72 → Orchestrator",
      "Reddit: BNB 负面情绪预警",
      "新闻: 机构流入信号检测",
      "Telegram: 社区活跃度突增",
    ],
  },
  {
    id: "risk",
    name: "风险管理",
    role: "VaR · 仓位控制",
    description: "实时监控风险指标，执行仓位限制、止损策略和组合优化。自动触发风险预警。",
    status: "告警",
    tagColor: "var(--color-amber)",
    tagBg: "var(--color-amber-dim)",
    metrics: [
      { label: "风险水位", value: "63.1%", color: "text-amber" },
      { label: "日VaR(95%)", value: "-2.1%", color: "text-amber" },
      { label: "最大回撤", value: "-4.2%", color: "text-amber" },
      { label: "预警次数", value: "3", color: "text-amber" },
    ],
    logs: [
      "⚠ BTC 仓位集中度超限 38%",
      "仓位上限预警 → 执行",
      "VaR 模型更新: 2.1%",
      "止损触发: MATIC @0.701",
    ],
  },
  {
    id: "strategy",
    name: "策略优化",
    role: "参数调优 · 回测",
    description: "基于历史数据进行参数优化和策略回测，自动调整策略参数以适应当前市场环境。",
    status: "运行中",
    tagColor: "var(--color-green)",
    tagBg: "var(--color-green-dim)",
    metrics: [
      { label: "优化进度", value: "91.5%", color: "text-green" },
      { label: "回测次数", value: "1,284", color: "" },
      { label: "最佳夏普", value: "2.41", color: "text-green" },
      { label: "运行策略", value: "3", color: "" },
    ],
    logs: [
      "动量突破: 参数更新 → +2.1%",
      "回测完成: 近 90 天数据",
      "均值回归: 等待部署确认",
      "套利对冲: 初始回测中...",
    ],
  },
  {
    id: "execution",
    name: "交易执行",
    role: "订单路由 · 滑点控制",
    description: "智能订单路由，最优执行价格选择，滑点控制和订单拆分。支持 Binance/OKX/Bybit。",
    status: "就绪",
    tagColor: "var(--color-green)",
    tagBg: "var(--color-green-dim)",
    metrics: [
      { label: "执行质量", value: "96.8%", color: "text-green" },
      { label: "平均滑点", value: "0.02%", color: "text-green" },
      { label: "成交率", value: "99.9%", color: "text-green" },
      { label: "今日订单", value: "47", color: "" },
    ],
    logs: [
      "BTC BUY 0.15 @67,820 (滑点 0.02%)",
      "ETH SELL 2.0 @3,418 已成交",
      "SOL BUY 25 @181.5 已成交",
      "MATIC SHORT 800 @0.692 已成交",
    ],
  },
];

export default function AgentsPage() {
  const [selectedAgent, setSelectedAgent] = useState(AGENT_DETAILS[0]);
  const [activeIdx, setActiveIdx] = useState(0);

  return (
    <DashboardShell>
      <div className="flex items-center gap-2 mb-1">
        <h1 className="text-sm font-bold">智能体管理</h1>
        <span className="text-xs text-text3">·</span>
        <span className="text-xs text-text3">6 个活跃 Agent</span>
      </div>

      {/* Agent detail list */}
      <div className="flex flex-col gap-3">
        {AGENT_DETAILS.map((agent, idx) => (
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
                  className="w-2.5 h-2.5 rounded-full animate-pulse-slow"
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
                <p className="text-xs text-text2 mb-3">{agent.description}</p>

                {/* Metrics */}
                <div className="grid grid-cols-4 gap-2 mb-3">
                  {agent.metrics.map((m, i) => (
                    <div key={i} className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3 py-2">
                      <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold mb-1">{m.label}</div>
                      <div className={`font-mono text-base font-semibold ${m.color || "text-text"}`}>{m.value}</div>
                    </div>
                  ))}
                </div>

                {/* Recent logs */}
                <div className="font-mono text-xs">
                  <div className="text-[9px] text-text3 uppercase tracking-wider font-semibold mb-1.5">最近活动</div>
                  {agent.logs.map((log, i) => (
                    <div key={i} className="text-text2 py-0.5 border-b border-[rgba(255,255,255,0.03)] last:border-0">
                      {log}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </DashboardShell>
  );
}
