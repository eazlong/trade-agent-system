"use client";

import { useState, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
}

const AGENTS = [
  { name: "市场分析", bg: "var(--color-teal-dim)", color: "var(--color-teal)", border: "rgba(100,255,218,0.3)" },
  { name: "情绪分析", bg: "var(--color-blue-dim)", color: "var(--color-blue)", border: "rgba(64,196,255,0.3)" },
  { name: "风险管理", bg: "var(--color-amber-dim)", color: "var(--color-amber)", border: "rgba(255,171,0,0.3)" },
  { name: "策略优化", bg: "var(--color-green-dim)", color: "var(--color-green)", border: "rgba(0,230,118,0.3)" },
  { name: "回测引擎", bg: "var(--color-bg2)", color: "var(--color-text3)", border: "var(--color-border)" },
];

export default function CreateStrategyModal({ open, onClose }: ModalProps) {
  const [selectedAgents, setSelectedAgents] = useState([0, 1, 2, 3]);

  const toggleAgent = (idx: number) => {
    setSelectedAgents((prev) =>
      prev.includes(idx) ? prev.filter((i) => i !== idx) : [...prev, idx]
    );
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/70 z-[100] flex items-center justify-center backdrop-blur-sm" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-bg1 border border-[rgba(255,255,255,0.12)] rounded-xl p-6 w-[480px] max-w-[90vw]">
        <div className="flex items-center justify-between mb-4">
          <span className="text-base font-bold">创建新策略</span>
          <button onClick={onClose} className="cursor-pointer text-text2 text-lg leading-none hover:text-text transition-colors">✕</button>
        </div>

        <div className="mb-3">
          <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1">策略名称</label>
          <input
            type="text"
            placeholder="例：BTC 高频动量策略"
            className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none focus:border-green transition-colors"
          />
        </div>

        <div className="mb-3">
          <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1">交易品种</label>
          <select className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none cursor-pointer">
            <option>BTC/USDT</option>
            <option>ETH/USDT</option>
            <option>SOL/USDT</option>
            <option>多品种组合</option>
          </select>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1">策略类型</label>
            <select className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none cursor-pointer">
              <option>趋势跟踪</option>
              <option>均值回归</option>
              <option>套利对冲</option>
              <option>市场中性</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1">时间周期</label>
            <select className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none cursor-pointer">
              <option>1m</option>
              <option>5m</option>
              <option>15m</option>
              <option>1h</option>
              <option>4h</option>
            </select>
          </div>
        </div>

        <div className="mb-4">
          <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1">启用智能体</label>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {AGENTS.map((agent, i) => (
              <span
                key={i}
                onClick={() => toggleAgent(i)}
                className="px-2.5 py-1 rounded-md text-xs font-semibold cursor-pointer transition-all border"
                style={{
                  background: selectedAgents.includes(i) ? agent.bg : "var(--color-bg2)",
                  color: agent.color,
                  borderColor: selectedAgents.includes(i) ? agent.border : "var(--color-border)",
                }}
              >
                {agent.name}
              </span>
            ))}
          </div>
        </div>

        <div className="flex gap-2.5 justify-end">
          <button onClick={onClose} className="px-4.5 py-2 rounded-lg text-xs font-semibold bg-transparent border border-[rgba(255,255,255,0.12)] text-text2 hover:bg-bg2 hover:text-text transition-all cursor-pointer">
            取消
          </button>
          <button onClick={onClose} className="px-4.5 py-2 rounded-lg text-xs font-semibold bg-green text-black hover:opacity-85 transition-all cursor-pointer">
            先回测，再部署
          </button>
        </div>
      </div>
    </div>
  );
}
