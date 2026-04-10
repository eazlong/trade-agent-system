"use client";

import { useState, useEffect, useCallback } from "react";

interface LogEntry {
  time: string;
  agent: string;
  color: string;
  message: string;
}

const LOG_TEMPLATES = [
  { color: "var(--color-purple)", agent: "Orchestrator", arrow: "→ 市场分析", msg: "触发扫描任务 · BTC 信号强度 HIGH" },
  { color: "var(--color-teal)", agent: "市场分析", arrow: "→ 风险管理", msg: "RSI 超买预警 · 建议减仓 10%" },
  { color: "var(--color-blue)", agent: "情绪分析", arrow: "→ Orchestrator", msg: "Twitter 情绪分 +72 · 多头偏强" },
  { color: "var(--color-amber)", agent: "风险管理", arrow: "→ 执行", msg: "仓位上限预警 · BTC 敞口 38%" },
  { color: "var(--color-green)", agent: "策略优化", arrow: "→ Orchestrator", msg: "参数更新完成 · 预期收益 +2.1%" },
  { color: "var(--color-green)", agent: "执行", arrow: "→ Orchestrator", msg: "订单成交 BTC 0.15 · 滑点 0.02%" },
  { color: "var(--color-purple)", agent: "Orchestrator", arrow: "→ 情绪分析", msg: "请求新闻快速扫描 · 优先级 HIGH" },
  { color: "var(--color-teal)", agent: "市场分析", arrow: "→ 策略优化", msg: "SOL 突破确认 · 信号置信 84%" },
];

function highlightText(text: string) {
  return text.split(/(HIGH|LOW|\+[\d.]+%|-[\d.]+%|[\d.]+%|[\d.]+)/g).map((part, i) => {
    if (/^(HIGH|LOW|\+[\d.]+%|-[\d.]+%|[\d.]+%|[\d.]+)$/.test(part)) {
      return <span key={i} className="text-text font-semibold">{part}</span>;
    }
    return part;
  });
}

export default function LogList() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [idx, setIdx] = useState(0);

  const addLog = useCallback(() => {
    const t = LOG_TEMPLATES[idx % LOG_TEMPLATES.length];
    setIdx((prev) => prev + 1);
    const now = new Date();
    const time = now.toTimeString().slice(0, 8);
    setLogs((prev) => [
      { time, agent: t.agent, color: t.color, message: `${t.arrow} · ${t.msg}` },
      ...prev.slice(0, 19),
    ]);
  }, [idx]);

  useEffect(() => {
    // Initial logs
    for (let i = 0; i < 8; i++) {
      const t = LOG_TEMPLATES[i % LOG_TEMPLATES.length];
      const now = new Date();
      now.setSeconds(now.getSeconds() - (8 - i) * 3);
      const time = now.toTimeString().slice(0, 8);
      setLogs((prev) => [...prev, { time, agent: t.agent, color: t.color, message: `${t.arrow} · ${t.msg}` }]);
    }
  }, []);

  useEffect(() => {
    const timer = setInterval(addLog, 3200);
    return () => clearInterval(timer);
  }, [addLog]);

  return (
    <div className="font-mono text-xs flex flex-col gap-0.5">
      {logs.map((log, i) => (
        <div key={i} className="flex gap-2.5 py-0.5">
          <span className="text-text3 flex-shrink-0">{log.time}</span>
          <span className="flex-shrink-0 min-w-[100px]" style={{ color: log.color }}>{log.agent}</span>
          <span className="text-text2">{highlightText(log.message)}</span>
        </div>
      ))}
    </div>
  );
}
