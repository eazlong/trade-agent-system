"use client";

import { useState, useEffect } from "react";

export type FilterType = "all" | "grid" | "single";

export interface FilterBarValue {
  search: string;
  filterType: FilterType;
}

interface BacktestFilterBarProps {
  onChange: (values: FilterBarValue) => void;
}

export default function BacktestFilterBar({ onChange }: BacktestFilterBarProps) {
  const [search, setSearch] = useState("");
  const [filterType, setFilterType] = useState<FilterType>("all");

  useEffect(() => {
    onChange({ search, filterType });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, filterType]);

  return (
    <div className="flex gap-3 mb-4">
      <div className="flex-1">
        <input
          type="text"
          placeholder="搜索策略名 / 品种..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full px-3 py-2 text-xs bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-lg text-text placeholder:text-text3 focus:outline-none focus:border-green/50 font-mono"
        />
      </div>
      <select
        value={filterType}
        onChange={(e) => setFilterType(e.target.value as FilterType)}
        className="px-3 py-2 text-xs bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-lg text-text focus:outline-none focus:border:border-green/50 font-mono cursor-pointer"
      >
        <option value="all">全部类型</option>
        <option value="grid">网格搜索</option>
        <option value="single">单次回测</option>
      </select>
    </div>
  );
}