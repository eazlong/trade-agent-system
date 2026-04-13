"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { useRightPanel } from "@/components/layout/DashboardShell";

const NAV_ITEMS = [
  { key: "overview", label: "总览" },
  { key: "agents", label: "智能体" },
  { key: "trading", label: "交易" },
  { key: "logs", label: "日志" },
  { key: "settings", label: "配置" },
];

export default function Topbar() {
  const pathname = usePathname();
  const activeTab = pathname.split("/").pop() || "overview";
  const { show, toggle } = useRightPanel();
  const [markets, setMarkets] = useState([
    { sym: "BTC/USDT", price: 67842, chg: 1.24 },
    { sym: "ETH/USDT", price: 3421, chg: 0.87 },
    { sym: "SOL/USDT", price: 182.4, chg: -0.43 },
  ]);

  useEffect(() => {
    const timer = setInterval(() => {
      setMarkets((prev) =>
        prev.map((m) => ({
          ...m,
          price: m.price + (Math.random() - 0.5) * m.price * 0.001,
          chg: m.chg + (Math.random() - 0.5) * 0.05,
        }))
      );
    }, 1800);
    return () => clearInterval(timer);
  }, []);

  const formatPrice = (price: number, sym: string) => {
    if (sym.startsWith("BTC")) return price.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    if (sym.startsWith("ETH")) return price.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return price.toFixed(1);
  };

  return (
    <div className="fixed top-0 left-0 right-0 h-[52px] bg-bg1 border-b border-[rgba(255,255,255,0.07)] flex items-center px-4 z-50 gap-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 pr-5 border-r border-[rgba(255,255,255,0.07)] mr-5">
        <div className="w-7 h-7 bg-gradient-to-br from-green to-teal rounded-md flex items-center justify-center">
          <svg viewBox="0 0 16 16" fill="none" className="w-4 h-4">
            <path d="M8 1L14 4.5V11.5L8 15L2 11.5V4.5L8 1Z" stroke="#000" strokeWidth="1.5" />
            <circle cx="8" cy="8" r="2" fill="#000" />
          </svg>
        </div>
        <div>
          <div className="text-sm font-bold tracking-wide">TradeClaw</div>
          <div className="text-[9px] font-mono text-text3 uppercase tracking-widest -mt-0.5">
            Agent OS · v2.4.1
          </div>
        </div>
      </div>

      {/* Nav tabs */}
      <div className="flex gap-0.5">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.key}
            href={`/${item.key}`}
            className={`px-3.5 py-1.5 text-xs font-medium rounded-md cursor-pointer transition-all border border-transparent ${
              activeTab === item.key
                ? "text-green bg-green-dim border-green/20"
                : "text-text2 hover:text-text hover:bg-bg2"
            }`}
          >
            {item.label}
          </Link>
        ))}
      </div>

      {/* Right section */}
      <div className="ml-auto flex items-center gap-3">
        {markets.map((m) => (
          <div
            key={m.sym}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md font-mono text-xs"
          >
            <span className="text-text2">{m.sym.split("/")[0]}</span>
            <span className="font-semibold">{formatPrice(m.price, m.sym)}</span>
            <span className={`font-semibold ${m.chg >= 0 ? "text-green" : "text-red"}`}>
              {m.chg >= 0 ? "+" : ""}
              {m.chg.toFixed(2)}%
            </span>
          </div>
        ))}

        {/* Status */}
        <div className="flex items-center gap-1.5 ml-2">
          <div className="w-1.5 h-1.5 rounded-full bg-green animate-pulse-slow" style={{ boxShadow: "0 0 6px var(--color-green)" }} />
          <span className="text-xs text-text2 font-medium">实时运行中</span>
        </div>

        {/* Right panel toggle */}
        <button
          onClick={toggle}
          className={`ml-1 p-1.5 rounded-md cursor-pointer transition-all border ${
            show ? "bg-green-dim border-green/30 text-green" : "bg-bg2 border-[rgba(255,255,255,0.07)] text-text2 hover:text-text hover:bg-bg3"
          }`}
          title={show ? "收起右侧面板" : "展开右侧面板"}
        >
          {show ? (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <rect x="1" y="2" width="12" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
              <line x1="9" y1="2" x2="9" y2="12" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <rect x="1" y="2" width="12" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
              <line x1="9" y1="2" x2="9" y2="12" stroke="currentColor" strokeWidth="1.2" strokeDasharray="2 2" />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}
