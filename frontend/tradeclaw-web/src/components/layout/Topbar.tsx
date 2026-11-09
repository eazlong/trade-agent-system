"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import dynamic from "next/dynamic";
import { useState, useEffect, useRef, useCallback } from "react";
import { useRightPanel } from "@/components/layout/DashboardShell";
import { useHeaderSymbols } from "@/hooks/useHeaderSymbols";
import { formatHeaderPrice } from "@/lib/headerSymbols";
import { marketApi, notificationApi, type Notification } from "@/lib/api";

/** K 线弹窗按需加载：lightweight-charts 只在真的点开某个行情条时才进包。
 *  顶栏是每页都在的常驻组件，不为它扛一整张图表库。 */
const SymbolKlineModal = dynamic(
  () => import("@/components/market/SymbolKlineModal"),
  { ssr: false }
);

const NAV_ITEMS = [
  { key: "overview", label: "总览" },
  { key: "agents", label: "智能体" },
  { key: "backtest", label: "回测" },
  { key: "trading", label: "交易" },
  // 机制读面（P5 map #40）。放在「交易」之后：它是运维读面，先看仓、再看机制为什么拦。
  { key: "regime", label: "机制" },
  { key: "workflows", label: "工作流" },
  { key: "logs", label: "日志" },
  { key: "settings", label: "配置" },
];

/**
 * 顶栏实时行情品种 **不再是常量**：它是用户级偏好（`User.header_symbols`，
 * 在「配置 → 通用 → 顶栏行情币种」里改，3–5 个，默认 BTC/ETH/SOL/BNB）。
 *
 * 这里只消费 `useHeaderSymbols()`，不自己存一份默认值——默认值/归一/边界都在
 * `lib/headerSymbols.ts` 与后端 `apps/authentication/preferences.py`，同一件事只许有一个说法。
 *
 * 与订阅清单的关系仍然是要命的那一条：`GET /api/datasource/api/market/ticker/` 只从
 * WS 内存存储取数、不做 REST 回退，没订阅的品种永远是一个「—」（2026-11-05 加 BNB 时
 * 撞上过：前端画了四档，WS 只订阅了三档）。用户把某个品种配进顶栏时，补订阅由
 * 偏好接口负责（读/写两条路径都会调 `ensure_feed_symbols`，幂等），不靠前端。
 */

interface Quote {
  price: number | null;
  chg: number | null;
}

export default function Topbar() {
  const pathname = usePathname();
  const activeTab = pathname.split("/").pop() || "overview";
  const { show, toggle } = useRightPanel();
  const { symbols } = useHeaderSymbols();
  const [unreadCount, setUnreadCount] = useState(0);
  const [showNotifDropdown, setShowNotifDropdown] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  /** 按品种存报价，而不是按「第几条」存：用户把清单从 [BTC,ETH,SOL] 改成
   *  [BTC,ETH,BNB] 时，BTC/ETH 已有的价格应当留在原地（而不是先闪一帧「—」）。 */
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});

  // 点开的行情条 → 弹 K 线。null = 没开。
  const [klineSymbol, setKlineSymbol] = useState<string | null>(null);
  const closeKline = useCallback(() => setKlineSymbol(null), []);

  useEffect(() => {
    if (symbols.length === 0) return;
    let cancelled = false;

    // 取数函数定义在 effect 里（而不是 useCallback）：它只服务这一个轮询循环，
    // 依赖只有 `symbols`，不需要一个组件级稳定的引用。
    const tickQuotes = async () => {
      const results = await Promise.all(
        symbols.map(async (sym) => {
          try {
            const t = await marketApi.ticker({ source: "binance", symbol: sym });
            const price = Number(t.last_price);
            const chg = Number(t.change_pct_24h);
            // Only accept a real, finite ticker; otherwise keep the previous value.
            if (!Number.isFinite(price) || !Number.isFinite(chg)) return null;
            return { sym, price, chg };
          } catch {
            // keep the last good value for this symbol on transient errors
            return null;
          }
        })
      );
      if (cancelled) return;
      setQuotes((prev) => {
        const next = { ...prev };
        for (const r of results) {
          if (r) next[r.sym] = { price: r.price, chg: r.chg };
        }
        return next;
      });
    };

    void tickQuotes();
    const timer = setInterval(() => void tickQuotes(), 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [symbols]);

  // Fetch unread count
  useEffect(() => {
    notificationApi.unreadCount().then((res) => setUnreadCount(res.unread_count)).catch(() => {});
    const timer = setInterval(() => {
      notificationApi.unreadCount().then((res) => setUnreadCount(res.unread_count)).catch(() => {});
    }, 30000);
    return () => clearInterval(timer);
  }, []);

  // Close dropdown on outside click
  const notifRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!showNotifDropdown) return;
    const handler = (e: MouseEvent) => {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setShowNotifDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showNotifDropdown]);

  const handleNotifClick = async () => {
    if (showNotifDropdown) {
      setShowNotifDropdown(false);
      return;
    }
    setShowNotifDropdown(true);
    try {
      const list = await notificationApi.list();
      setNotifications(list);
      if (unreadCount > 0) {
        await notificationApi.markAllRead();
        setUnreadCount(0);
      }
    } catch { /* ignore */ }
  };

  const fmtTime = (d: string) => {
    const now = new Date();
    const ts = new Date(d);
    const diff = (now.getTime() - ts.getTime()) / 1000;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
    return ts.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const formatPrice = (price: number) => formatHeaderPrice(price);

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
        {symbols.map((sym) => {
          const q = quotes[sym];
          const price = q?.price ?? null;
          const chg = q?.chg ?? null;
          return (
            <button
              type="button"
              key={sym}
              onClick={() => setKlineSymbol(sym)}
              title={`查看 ${sym} K 线`}
              className="flex items-center gap-1.5 px-2.5 py-1 bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md font-mono text-xs cursor-pointer transition-all hover:bg-bg3 hover:border-green/30"
            >
              <span className="text-text2">{sym.split("/")[0]}</span>
              <span className="font-semibold">{price === null ? "—" : formatPrice(price)}</span>
              <span className={`font-semibold ${chg === null ? "text-text2" : chg >= 0 ? "text-green" : "text-red"}`}>
                {chg === null ? "—" : `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`}
              </span>
            </button>
          );
        })}

        {/* Notification bell */}
        <div className="relative" ref={notifRef}>
          <button
            onClick={handleNotifClick}
            className="p-1.5 rounded-md cursor-pointer transition-all border bg-bg2 border-[rgba(255,255,255,0.07)] text-text2 hover:text-text hover:bg-bg3"
            title="通知"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M11 5C11 3.34 9.66 2 8 2H6C4.34 2 3 3.34 3 5L2 10H12L11 5Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
              <path d="M6 12C6 12.55 6.45 13 7 13C7.55 13 8 12.55 8 12" stroke="currentColor" strokeWidth="1.2"/>
            </svg>
            {unreadCount > 0 && (
              <span className="absolute -top-1 -right-1 w-3.5 h-3.5 bg-red rounded-full text-[8px] font-bold text-white flex items-center justify-center">
                {unreadCount > 9 ? "9+" : unreadCount}
              </span>
            )}
          </button>

          {showNotifDropdown && (
            <div className="absolute right-0 top-full mt-2 w-80 bg-bg1 border border-[rgba(255,255,255,0.1)] rounded-lg shadow-xl z-50 max-h-96 overflow-y-auto">
              <div className="sticky top-0 bg-bg1 px-3 py-2.5 border-b border-[rgba(255,255,255,0.07)]">
                <span className="text-xs font-bold text-text">通知</span>
              </div>
              {notifications.length === 0 ? (
                <div className="px-4 py-8 text-center text-xs text-text3 font-mono">暂无通知</div>
              ) : (
                notifications.map((n) => (
                  <div
                    key={n.id}
                    className="px-3 py-2.5 border-b border-[rgba(255,255,255,0.04)] hover:bg-bg2/50 transition-colors"
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${n.channel === "telegram" ? "bg-blue-dim text-blue" : "bg-green-dim text-green"}`}>
                        {n.channel === "telegram" ? "TG" : "Web"}
                      </span>
                      <span className="text-[10px] text-text3 font-mono">{fmtTime(n.created_at)}</span>
                    </div>
                    <div className="text-xs text-text leading-snug">{n.message}</div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>

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

      {/* K 线对话框（点行情条打开） */}
      {klineSymbol && (
        <SymbolKlineModal symbol={klineSymbol} onClose={closeKline} />
      )}
    </div>
  );
}
