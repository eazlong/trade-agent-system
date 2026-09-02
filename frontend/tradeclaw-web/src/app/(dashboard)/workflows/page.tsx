"use client";

import { useState, useEffect, useCallback } from "react";
import DashboardShell from "@/components/layout/DashboardShell";
import { workflowApi, WorkflowHistoryItem, WorkflowHistoryDetail } from "@/lib/api";
import Link from "next/link";

const STATUS_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  completed: { bg: "bg-green-dim", text: "text-green", label: "已完成" },
  failed: { bg: "bg-red-dim", text: "text-red", label: "失败" },
  aborted: { bg: "bg-amber-dim", text: "text-amber", label: "已中止" },
};

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const min = Math.floor(seconds / 60);
  const sec = (seconds % 60).toFixed(0);
  return `${min}m ${sec}s`;
}

function timeAgo(dateStr: string): string {
  const now = Date.now();
  const date = new Date(dateStr).getTime();
  const diff = now - date;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

export default function WorkflowsPage() {
  const [items, setItems] = useState<WorkflowHistoryItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WorkflowHistoryDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await workflowApi.list({ page, status: statusFilter || undefined });
      setItems(res.items ?? []);
      setTotalCount(res.count);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "加载工作流失败";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const fetchDetail = async (workflowId: string) => {
    if (expandedId === workflowId) {
      setExpandedId(null);
      setDetail(null);
      setDetailError(null);
      return;
    }
    setDetailError(null);
    try {
      const data = await workflowApi.getDetail(workflowId);
      setExpandedId(workflowId);
      setDetail(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "加载详情失败";
      setDetailError(msg);
    }
  };

  const totalPages = Math.max(1, Math.ceil(totalCount / 20));

  return (
    <DashboardShell>
      <div className="space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-sm font-bold text-text">工作流历史</h1>
            <p className="text-xs text-text3 mt-0.5">查看工作流执行记录与详细步骤</p>
          </div>
          <div className="text-xs text-text2">{totalCount} 条记录</div>
        </div>

        {/* Filters */}
        <div className="flex gap-2">
          {["", "completed", "failed", "aborted"].map((s) => {
            const label = s ? STATUS_COLORS[s]?.label : "全部";
            const active = statusFilter === s;
            return (
              <button
                key={s}
                onClick={() => {
                  setStatusFilter(s);
                  setPage(1);
                }}
                className={`px-3 py-1.5 text-xs font-medium rounded-md cursor-pointer transition-all border ${
                  active
                    ? "text-green bg-green-dim border-green/20"
                    : "text-text2 hover:text-text hover:bg-bg2 border-transparent"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>

        {/* Table */}
        <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
          {/* Header */}
          <div className="grid grid-cols-12 gap-2 px-4 py-2.5 border-b border-[rgba(255,255,255,0.07)] text-[10px] font-semibold text-text3 uppercase tracking-wider">
            <div className="col-span-2">状态</div>
            <div className="col-span-4">摘要</div>
            <div className="col-span-1">步骤</div>
            <div className="col-span-1">耗时</div>
            <div className="col-span-2">时间</div>
            <div className="col-span-1">操作</div>
          </div>

          {loading ? (
            <div className="px-4 py-8 text-center text-text3 text-xs">加载中...</div>
          ) : error ? (
            <div className="px-4 py-8 text-center">
              <div className="text-xs text-red mb-2">{error}</div>
              <button
                onClick={fetchData}
                className="px-3 py-1 text-xs rounded bg-red/10 text-red hover:bg-red/20 transition-colors"
              >
                重试
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="px-4 py-8 text-center text-text3 text-xs">暂无工作流记录</div>
          ) : (
            items.map((item) => {
              const st = STATUS_COLORS[item.status] || { bg: "bg-bg2", text: "text-text", label: item.status };
              const isExpanded = expandedId === item.workflow_id;
              return (
                <div key={item.id}>
                  <div
                    className={`grid grid-cols-12 gap-2 px-4 py-3 border-b border-[rgba(255,255,255,0.05)] text-xs cursor-pointer transition-colors hover:bg-bg2 ${
                      isExpanded ? "bg-bg2" : ""
                    }`}
                    onClick={() => fetchDetail(item.workflow_id)}
                  >
                    <div className="col-span-2">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${st.bg} ${st.text}`}>
                        {st.label}
                      </span>
                    </div>
                    <div className="col-span-4 text-text2 truncate" title={item.summary}>
                      {item.summary}
                    </div>
                    <div className="col-span-1 text-text2">
                      {item.completed_steps}/{item.total_steps}
                    </div>
                    <div className="col-span-1 text-text2 font-mono">
                      {formatDuration(item.elapsed_seconds)}
                    </div>
                    <div className="col-span-2 text-text3">
                      {timeAgo(item.created_at)}
                    </div>
                    <div className="col-span-1">
                      <button className="text-text3 hover:text-green text-[10px]">
                        {isExpanded ? "收起" : "详情"}
                      </button>
                    </div>
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && detailError && (
                    <div className="px-4 py-3 bg-bg1 border-b border-[rgba(255,255,255,0.05)]">
                      <div className="text-xs text-red bg-red/5 p-2 rounded border border-red/10">
                        {detailError}
                      </div>
                    </div>
                  )}

                  {isExpanded && detail && (
                    <div className="px-4 py-3 bg-bg1 border-b border-[rgba(255,255,255,0.05)]">
                      <div className="space-y-2">
                        {detail.step_results.map((sr) => {
                          const stepSt = sr.skipped
                            ? { bg: "bg-bg2", text: "text-text3", label: "跳过" }
                            : sr.failed
                            ? { bg: "bg-red-dim", text: "text-red", label: "失败" }
                            : { bg: "bg-green-dim", text: "text-green", label: "成功" };
                          return (
                            <div key={sr.step} className="flex items-start gap-2 text-xs">
                              <div className="w-16 text-text3 shrink-0">步骤 {sr.step}</div>
                              <div className="w-24 shrink-0">
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${stepSt.bg} ${stepSt.text}`}>
                                  {stepSt.label}
                                </span>
                              </div>
                              <div className="text-text2">{sr.agent}</div>
                            </div>
                          );
                        })}
                        {detail.error && (
                          <div className="mt-2 text-xs text-red bg-red/5 p-2 rounded border border-red/10">
                            错误: {detail.error}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between">
            <div className="text-xs text-text3">
              第 {page} / {totalPages} 页
            </div>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-3 py-1 text-xs rounded border border-[rgba(255,255,255,0.07)] bg-bg2 text-text2 disabled:opacity-30 hover:text-text"
              >
                上一页
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1 text-xs rounded border border-[rgba(255,255,255,0.07)] bg-bg2 text-text2 disabled:opacity-30 hover:text-text"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
