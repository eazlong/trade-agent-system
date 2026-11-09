"use client";

/**
 * 顶栏行情币种偏好的读写（`GET/PUT /api/auth/preferences/`）。
 *
 * 取值顺序：**localStorage 缓存 → 后端 GET → 以 GET 为准**。缓存不是真相，只用来在首帧
 * 少闪一次默认四档——顶栏是每页常驻的横条，每页挂载都从「四档默认值」跳到「用户配的
 * 三档」会很显眼。
 *
 * **GET 不是纯读**：后端在读路径上会顺手补 WS 订阅（`ensure_header_symbols_subscribed`，
 * 幂等、尽力而为）。ticker 端点只读 WS 内存存储、不做 REST 回退，所以这次调用是「用户配
 * 过的那几档在新进程/新机器里被重新订阅」的唯一机会。少跑一次，用户看到的就是永久的「—」。
 *
 * 写路径失败时**不动本地状态**：`save` 返回 false 并把后端 400 的原文留在 `error` 里
 * （那句原文已经说清了该怎么改，前端不按状态码自己翻译）。乐观更新在这里是错的——页面
 * 会显示一份「并没有被接受」的档位，而顶栏随后也照着它去取数。
 */
import { useCallback, useEffect, useState } from "react";
import { preferencesApi } from "@/lib/api";
import {
  DEFAULT_HEADER_SYMBOLS,
  normalizeHeaderSymbol,
  validateHeaderSymbols,
} from "@/lib/headerSymbols";

/** 顶栏币种清单在 localStorage 的缓存键（**只做首屏**，真相在后端）。 */
export const HEADER_SYMBOLS_CACHE_KEY = "tradeclaw_header_symbols";

/** 缓存里那份清单。任何读不懂/不合规的缓存都退回默认四档：
 *  条数不对的缓存会让顶栏先画错档位再跳到正确档位，比晚一帧更难看。 */
export function readCachedHeaderSymbols(): string[] {
  if (typeof window === "undefined") return [...DEFAULT_HEADER_SYMBOLS];
  try {
    const raw = window.localStorage.getItem(HEADER_SYMBOLS_CACHE_KEY);
    if (!raw) return [...DEFAULT_HEADER_SYMBOLS];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...DEFAULT_HEADER_SYMBOLS];
    const symbols = parsed.filter((x): x is string => typeof x === "string");
    if (validateHeaderSymbols(symbols) !== null) return [...DEFAULT_HEADER_SYMBOLS];
    return symbols;
  } catch {
    return [...DEFAULT_HEADER_SYMBOLS];
  }
}

/**
 * 清单变更的**进程内广播**。
 *
 * 为什么需要：`useHeaderSymbols` 有**两个独立实例**——顶栏一个（每页常驻）、配置页编辑器
 * 一个。用户在配置页保存之后，顶栏那一份 state 并不知道，于是「保存成功、顶栏还是老四档」，
 * 要换页（重挂载）才生效。实测就是这样：保存后顶栏仍显示 BNB，导航离开再回来才变。
 *
 * 仅靠 localStorage 的事件不够：`storage` 事件只在**别的标签页**触发，同页的两个 hook
 * 实例收不到。所以这里自己做一个极小的订阅表 —— 比引入状态库或把 hook 提升到 Provider
 * 都轻（顶栏在 DashboardShell 之外还挂在别处，Provider 方案要改布局）。
 */
type Listener = (symbols: string[]) => void;
const listeners = new Set<Listener>();

function emit(symbols: string[]): void {
  for (const listener of listeners) listener(symbols);
}

function writeCachedHeaderSymbols(symbols: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(HEADER_SYMBOLS_CACHE_KEY, JSON.stringify(symbols));
  } catch {
    // 隐私模式 / 配额满：写不进缓存不影响任何行为，真相在后端
  }
  // 同页的其他 hook 实例（尤其是顶栏）需要立刻跟上，而不是等下一次挂载
  emit(symbols);
}

export interface HeaderSymbolsState {
  /** 当前要画的档（首屏先给缓存/默认值，拿到后端后替换） */
  symbols: string[];
  /** 用户从没改过偏好（后端 `is_default`）——「恢复默认」据此置灰 */
  isDefault: boolean;
  /** 正在读后端（首屏为 true，用来避免在还没读到时就显示「保存」成功态） */
  loading: boolean;
  saving: boolean;
  /** 保存失败的那句话：后端 400 的原文，直接显示 */
  error: string | null;
  /** 保存整份清单。返回是否真的落库（false 时看 `error`）。 */
  save: (symbols: string[]) => Promise<boolean>;
  /** 清空错误（用户改了输入就该让上一轮的红字消失） */
  clearError: () => void;
}

export function useHeaderSymbols(): HeaderSymbolsState {
  const [symbols, setSymbols] = useState<string[]>(readCachedHeaderSymbols);
  const [isDefault, setIsDefault] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const listener: Listener = (next) => {
      setSymbols(next);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    preferencesApi
      .headerSymbols()
      .then((pref) => {
        if (!alive) return;
        setSymbols(pref.header_symbols);
        setIsDefault(pref.is_default === true);
        writeCachedHeaderSymbols(pref.header_symbols);
      })
      .catch(() => {
        // 未登录 / 后端不可达：保持缓存（默认四档）继续跑。
        // 顶栏不该因为偏好取不到而空掉——那会让一个「配置读失败」看起来像「行情全挂了」。
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const save = useCallback(async (next: string[]) => {
    const problem = validateHeaderSymbols(next);
    if (problem) {
      setError(problem);
      return false;
    }
    // 先归一（`btc` → `BTC/USDT`）再发：后端也会归一，但带着用户的原始写法回来时
    // 前端就要再归一一次才能比对——不如让请求里就是最终形态。
    const normalized = next.map((s) => normalizeHeaderSymbol(s) as string);
    setSaving(true);
    setError(null);
    try {
      const pref = await preferencesApi.saveHeaderSymbols(normalized);
      setSymbols(pref.header_symbols);
      setIsDefault(pref.is_default === true);
      writeCachedHeaderSymbols(pref.header_symbols);
      return true;
    } catch (e) {
      // 后端 400 的 `error` 原文（ApiError.message）——它已经说清了该怎么改
      setError(e instanceof Error ? e.message : "保存失败");
      return false;
    } finally {
      setSaving(false);
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return { symbols, isDefault, loading, saving, error, save, clearError };
}
