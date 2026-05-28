"use client";

import { useState } from "react";
import Link from "next/link";
import type { BacktestGroup, BacktestResult } from "@/lib/api";

interface BacktestTreeTableProps {
  groups: BacktestGroup[];
}

export default function BacktestTreeTable({ groups }: BacktestTreeTableProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fmtPct = (v: number | null | undefined, digits = 2) =>
    v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;

  const fmtNum = (v: number | null | undefined, digits = 2) =>
    v == null ? "—" : v.toFixed(digits);

  const fmtDate = (d: string) =>
    new Date(d).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });

  const renderParams = (params: Record<string, unknown>) => {
    if (!params || Object.keys(params).length === 0) return "无参数";
    const entries = Object.entries(params).slice(0, 3);
    const text = entries.map(([k, v]) => `${k}=${v}`).join(", ");
    const allText = Object.entries(params)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
    return (
      <span className="text-text3" title={allText}>
        {text}
        {Object.keys(params).length > 3 ? " ..." : ""}
      </span>
    );
  };

  const renderChildRow = (r: BacktestResult) => (
    <tr key={r.id} className="border-b border-[rgba(255,255,255,0.04)]">
      <td colSpan={11} className="p-0">
        <div className="ml-[30px] bg-bg2/50 rounded-md my-1 border border-[rgba(255,255,255,0.07)]">
          <div className="flex gap-4 p-3">
            {/* Left: Parameters */}
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-bold text-green mb-2 font-mono">
                📋 策略参数
              </div>
              <div className="flex flex-wrap gap-1.5">
                {r.parameters &&
                  Object.entries(r.parameters).map(([k, v]) => (
                    <span
                      key={k}
                      className="bg-bg1 px-2 py-1 rounded text-[11px] text-text font-mono"
                    >
                      {k}=<span className="text-green">{String(v)}</span>
                    </span>
                  ))}
              </div>
            </div>
            {/* Right: Full Metrics */}
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-bold text-green mb-2 font-mono">
                📊 完整指标
              </div>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { label: "收益率", value: fmtPct(r.total_return_pct), cls: r.total_return_pct != null && r.total_return_pct >= 0 ? "text-green" : "text-red" },
                  { label: "夏普", value: fmtNum(r.sharpe_ratio), cls: "text-text" },
                  { label: "最大回撤", value: fmtPct(r.max_drawdown_pct), cls: "text-red" },
                  { label: "Sortino", value: fmtNum(r.metrics?.sortino), cls: "text-text2" },
                  { label: "Calmar", value: fmtNum(r.metrics?.calmar), cls: "text-text2" },
                  { label: "Profit Factor", value: fmtNum(r.metrics?.profit_factor), cls: "text-text2" },
                ].map((m) => (
                  <div key={m.label} className="text-center">
                    <div className="text-[10px] text-text3">{m.label}</div>
                    <div className={`text-[14px] font-bold font-mono ${m.cls}`}>
                      {m.value}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </td>
    </tr>
  );

  if (groups.length === 0) {
    return (
      <div className="text-xs text-text3 py-8 text-center font-mono">
        暂无匹配的回测记录
      </div>
    );
  }

  return (
    <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-text3 border-b border-[rgba(255,255,255,0.07)]">
            <th className="w-[30px]"></th>
            <th className="text-left py-2.5 px-2 font-semibold">类型</th>
            <th className="text-left py-2.5 px-4 font-semibold">策略</th>
            <th className="text-left py-2.5 px-4 font-semibold">品种</th>
            <th className="text-left py-2.5 px-4 font-semibold">周期</th>
            <th className="text-right py-2.5 px-4 font-semibold">收益率</th>
            <th className="text-right py-2.5 px-4 font-semibold">夏普</th>
            <th className="text-right py-2.5 px-4 font-semibold">回撤</th>
            <th className="text-right py-2.5 px-4 font-semibold">交易数</th>
            <th className="text-left py-2.5 px-4 font-semibold">参数 (前3)</th>
            <th className="text-right py-2.5 px-4 font-semibold">操作</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => {
            if (group.type === "grid_search" || group.type === "orphaned_grid_search") {
              const isExpanded = expanded.has(group.job_id || "");
              const isOrphaned = group.type === "orphaned_grid_search";
              return (
                <GroupRow
                  key={group.job_id || `orphan-${group.created_at}`}
                  group={group}
                  isExpanded={isExpanded}
                  isOrphaned={isOrphaned}
                  onToggle={() => toggle(group.job_id || "")}
                  fmtPct={fmtPct}
                  fmtNum={fmtNum}
                  fmtDate={fmtDate}
                  renderParams={renderParams}
                  renderChildRow={renderChildRow}
                />
              );
            }

            // single
            const r = group.result!;
            return (
              <tr
                key={r.id}
                className="border-b border-[rgba(255,255,255,0.04)] hover:bg-bg2/50 transition-colors"
              >
                <td></td>
                <td className="py-2 px-2">
                  <span className="bg-[rgba(255,255,255,0.07)] text-text2 px-1.5 py-0.5 rounded text-[10px]">
                    单次
                  </span>
                </td>
                <td className="py-2 px-4 text-text font-semibold">{r.strategy_name}</td>
                <td className="py-2 px-4 text-text font-semibold">{r.symbol}</td>
                <td className="py-2 px-4 text-text2">{r.timeframe}</td>
                <td className={`py-2 px-4 text-right font-semibold ${r.total_return_pct >= 0 ? "text-green" : "text-red"}`}>
                  {fmtPct(r.total_return_pct)}
                </td>
                <td className="py-2 px-4 text-right text-text">{fmtNum(r.sharpe_ratio)}</td>
                <td className="py-2 px-4 text-right text-red">{fmtPct(r.max_drawdown_pct)}</td>
                <td className="py-2 px-4 text-right text-text2">{r.total_trades}</td>
                <td className="py-2 px-4">{renderParams(r.parameters)}</td>
                <td className="py-2 px-4 text-right">
                  <Link href={`/backtest/${r.id}`} className="text-green hover:underline cursor-pointer">
                    详情
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Separate sub-component to keep main component readable */
function GroupRow({
  group,
  isExpanded,
  isOrphaned,
  onToggle,
  fmtPct,
  fmtNum,
  fmtDate,
  renderParams,
  renderChildRow,
}: {
  group: BacktestGroup;
  isExpanded: boolean;
  isOrphaned: boolean;
  onToggle: () => void;
  fmtPct: (v: number | null | undefined) => string;
  fmtNum: (v: number | null | undefined) => string;
  fmtDate: (d: string) => string;
  renderParams: (params: Record<string, unknown>) => React.ReactNode;
  renderChildRow: (r: BacktestResult) => React.ReactNode;
}) {
  const borderColor = isOrphaned ? "border-gray-600" : "border-green/40";
  const bg = isOrphaned ? "bg-bg2/30" : "bg-bg2/40";
  const arrowColor = isOrphaned ? "text-gray-500" : "text-green";

  return (
    <>
      <tr
        className={`border-b ${borderColor} border-t-2 ${bg} hover:bg-bg2/60 transition-colors cursor-pointer`}
        onClick={onToggle}
      >
        <td className={`py-2 px-2 ${arrowColor} select-none`}>
          {isExpanded ? "▼" : "▶"}
        </td>
        <td className="py-2 px-2">
          <span className={`px-1.5 py-0.5 rounded text-[10px] ${isOrphaned ? "bg-gray-700 text-gray-400" : "bg-green/20 text-green"}`}>
            {isOrphaned ? "孤儿" : "网格"}
          </span>
        </td>
        <td className={`py-2 px-4 font-semibold ${isOrphaned ? "text-gray-500" : "text-green"}`}>
          {group.job_name}
        </td>
        <td className="py-2 px-4 text-text2">{group.symbol}</td>
        <td className="py-2 px-4 text-text2">{group.timeframe}</td>
        <td className="py-2 px-4 text-right text-green">{fmtPct(group.best_return_pct)}</td>
        <td className="py-2 px-4 text-right text-text">{fmtNum(group.best_sharpe)}</td>
        <td className="py-2 px-4 text-right text-text2">
          {group.completed}/{group.total_combinations}
        </td>
        <td className="py-2 px-4 text-text3">—</td>
        <td className="py-2 px-4 text-right text-text3">—</td>
        <td className="py-2 px-4 text-right">
          <Link
            href={`/settings?tab=grid-search`}
            className="text-green hover:underline cursor-pointer"
            onClick={(e) => e.stopPropagation()}
          >
            🔍 详情
          </Link>
        </td>
      </tr>

      {/* Summary row under collapsed group */}
      {!isExpanded && (
        <tr className={`border-b ${borderColor} ${bg}`}>
          <td colSpan={11} className="py-1.5 px-4 text-xs text-text3 font-mono pl-[40px]">
            最佳收益: <span className="text-green">{fmtPct(group.best_return_pct)}</span>
            {" | "}
            最佳夏普: <span className="text-green">{fmtNum(group.best_sharpe)}</span>
            {" | "}
            完成: {group.completed}/{group.total_combinations}
            {" | "}
            {fmtDate(group.created_at)}
          </td>
        </tr>
      )}

      {/* Expanded child rows */}
      {isExpanded &&
        group.results?.map((r) => renderChildRow(r))}
    </>
  );
}