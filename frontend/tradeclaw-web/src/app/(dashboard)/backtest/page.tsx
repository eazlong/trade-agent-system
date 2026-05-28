"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import DashboardShell from "@/components/layout/DashboardShell";
import { backtestApi, type BacktestGroup } from "@/lib/api";
import BacktestFilterBar, { type FilterType } from "@/components/backtest/BacktestFilterBar";
import BacktestTreeTable from "@/components/backtest/BacktestTreeTable";
import CreateStrategyModal from "@/components/dashboard/CreateStrategyModal";

const PAGE_SIZE = 10;

export default function BacktestListPage() {
  const [groups, setGroups] = useState<BacktestGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [groupCount, setGroupCount] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [filterType, setFilterType] = useState<FilterType>("all");

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    backtestApi
      .getGroupedList({ page, page_size: PAGE_SIZE })
      .then((data) => {
        setGroups(data.groups ?? []);
        setTotalPages(data.num_pages);
        setTotalCount(data.total_records);
        setGroupCount(data.group_count);
      })
      .catch((e) => {
        setError(e?.message || "获取回测数据失败，请稍后重试");
        setGroups([]);
      })
      .finally(() => setLoading(false));
  }, [page]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const hasChildMatch = useCallback((group: BacktestGroup, q: string) => {
    if (group.type === "grid_search" || group.type === "orphaned_grid_search") {
      return group.results?.some((r) =>
        r.strategy_name?.toLowerCase().includes(q) ||
        r.symbol?.toLowerCase().includes(q)
      );
    }
    return false;
  }, []);

  const { filteredGroups, expandedOnMatch } = useMemo(() => {
    if (!search.trim()) {
      return { filteredGroups: groups, expandedOnMatch: new Set<string>() };
    }

    const q = search.toLowerCase();
    const expanded = new Set<string>();
    const filtered = groups.filter((group) => {
      if (group.type === "grid_search" || group.type === "orphaned_grid_search") {
        const matchJobName = group.job_name?.toLowerCase().includes(q);
        const matchSymbol = group.symbol?.toLowerCase().includes(q);
        if (matchJobName || matchSymbol) return true;
        const childMatch = hasChildMatch(group, q);
        if (childMatch) {
          expanded.add(group.job_id || "");
          return true;
        }
        return false;
      }
      return (
        group.result?.strategy_name?.toLowerCase().includes(q) ||
        group.result?.symbol?.toLowerCase().includes(q)
      );
    });
    return { filteredGroups: filtered, expandedOnMatch: expanded };
  }, [groups, search, hasChildMatch]);

  const handleFilterChange = useCallback((values: { search: string; filterType: FilterType }) => {
    setSearch(values.search);
    setFilterType(values.filterType);
  }, []);

  return (
    <DashboardShell>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-text">回测记录</h1>
          <p className="text-xs text-text3 mt-1">查看历史回测结果与详细分析</p>
        </div>
        <button
          onClick={() => setModalOpen(true)}
          className="bg-green text-black text-xs font-semibold px-4 py-2 rounded-lg shadow-lg hover:opacity-85 transition-all cursor-pointer"
        >
          + 新建策略
        </button>
      </div>

      <BacktestFilterBar onChange={handleFilterChange} />

      {loading ? (
        <div className="text-xs text-text3 py-8 text-center">加载中...</div>
      ) : error ? (
        <div className="text-xs text-red py-8 text-center font-mono">
          {error}
          <button
            onClick={fetchData}
            className="ml-3 text-green hover:underline cursor-pointer"
          >
            重试
          </button>
        </div>
      ) : (
        <>
          <BacktestTreeTable groups={filteredGroups} initialExpanded={expandedOnMatch} />
          {filteredGroups.length > 0 && totalPages > 1 && (
            <div className="flex items-center justify-between mt-3 text-xs font-mono">
              <span className="text-text3">
                共 {totalCount} 条 / {groupCount} 组，第 {page}/{totalPages} 页
              </span>
              <div className="flex gap-1.5">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-2.5 py-1 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  上一页
                </button>
                {page > 2 && (
                  <button
                    onClick={() => setPage(1)}
                    className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
                  >
                    1
                  </button>
                )}
                {page > 3 && (
                  <span className="px-1 py-1 text-text3">…</span>
                )}
                {page > 1 && (
                  <button
                    onClick={() => setPage(page - 1)}
                    className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
                  >
                    {page - 1}
                  </button>
                )}
                <span className="w-8 h-7 flex items-center justify-center rounded-md bg-green/10 border border-green/30 text-green font-semibold">
                  {page}
                </span>
                {page < totalPages && (
                  <button
                    onClick={() => setPage(page + 1)}
                    className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
                  >
                    {page + 1}
                  </button>
                )}
                {page < totalPages - 2 && (
                  <span className="px-1 py-1 text-text3">…</span>
                )}
                {page < totalPages - 1 && (
                  <button
                    onClick={() => setPage(totalPages)}
                    className="w-8 h-7 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text transition-colors"
                  >
                    {totalPages}
                  </button>
                )}
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-2.5 py-1 rounded-md bg-bg1 border border-[rgba(255,255,255,0.07)] text-text2 hover:text-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </>
      )}

      <CreateStrategyModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={() => { setPage(1); fetchData(); }}
      />
    </DashboardShell>
  );
}
