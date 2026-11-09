/**
 * `request()` 的错误形状（P5 子票 #46）。
 *
 * 这一层的全部风险在「后端说的那句话能不能到人眼前」。改之前 `request()` 把所有非 2xx
 * 压成一句 `API error: 400`，于是后端精心写的「切换原因不能为空」在页面上永远看不到，
 * 而前端要显示原因只能靠状态码猜。
 *
 * 四条契约，各自对应一个真实的失败形态：
 *
 *   1. **`ApiError` 带 `status` 与后端原文** —— 否则「为什么不行」只剩一个数字。
 *   2. **`ApiError` 是 `Error` 的子类** —— 全仓几十处 `err instanceof Error ? err.message`
 *      靠它；不继承，那些地方会静默退化成「未知错误」（本仓最贵的那类错：没有报错路径）。
 *   3. **非 JSON 的响应体不许让 `request()` 自己炸** —— 后端 500 白页、反代 HTML 错误页
 *      解析出来是 `SyntaxError`，那会把「500」这个真原因替换成「JSON 解析失败」。
 *   4. **401 刷新流程原样保留** —— 刷新成功后重试、刷新失败才跳登录。这段代码是最不该
 *      被顺手重写的一段，所以用行为把它钉住。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const ACCESS_KEY = "tradeclaw_access";
const REFRESH_KEY = "tradeclaw_refresh";

/** 一个最小的 `Response` 替身：只实现 `request()` 真正读的那几样。 */
function response(body: string, init: { ok?: boolean; status?: number } = {}) {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? (status >= 200 && status < 300),
    status,
    text: async () => body,
    json: async () => JSON.parse(body),
  };
}

describe("request() 的错误形状", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    localStorage.clear();
  });

  it("400 + {error} → ApiError 的 message 是后端那句话，status 是 400", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(JSON.stringify({ error: "切换原因不能为空" }), { status: 400 })
      )
    );
    const { regimeApi, ApiError } = await import("@/lib/api");

    const failure = await regimeApi.switchMode("mechanism", "executing").catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toBeInstanceOf(Error);
    expect(failure.message).toBe("切换原因不能为空");
    expect(failure.status).toBe(400);
  });

  it("403 + {detail} → 用 detail（权限类是这么回话的）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(JSON.stringify({ detail: "此操作需要管理员权限。" }), { status: 403 })
      )
    );
    const { regimeApi, ApiError } = await import("@/lib/api");

    const failure = await regimeApi.switchMode("mechanism", "executing").catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("此操作需要管理员权限。");
    expect(failure.status).toBe(403);
  });

  it("HTML 响应体（反代错误页）→ 退回 API error: 500，不是 SyntaxError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response("<!doctype html><html><body>502 Bad Gateway</body></html>", {
          status: 500,
        })
      )
    );
    const { regimeApi, ApiError } = await import("@/lib/api");

    const failure = await regimeApi.getOverview().catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("API error: 500");
    expect(failure.status).toBe(500);
    // 关键：不是 `SyntaxError: Unexpected token '<'`。那会把真原因（500）换掉。
    expect(failure).not.toBeInstanceOf(SyntaxError);
  });

  it("空响应体（204 之外的裸状态码）→ 同样退回 API error: <status>", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response("", { status: 502 })));
    const { regimeApi, ApiError } = await import("@/lib/api");

    const failure = await regimeApi.getPool().catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("API error: 502");
  });

  it("JSON 但不是 {error}/{detail}（DRF 字段校验错）→ 退回 API error: <status>", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(JSON.stringify({ email: ["这个字段是必填项。"] }), { status: 400 })
      )
    );
    const { authApi, ApiError } = await import("@/lib/api");

    const failure = await authApi.login({ email: "", password: "" }).catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("API error: 400");
  });

  it("成功路径不受影响：2xx 照旧返回解析后的 JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response(JSON.stringify({ ok: true }), { status: 200 }))
    );
    const { regimeApi } = await import("@/lib/api");

    await expect(regimeApi.getOverview()).resolves.toEqual({ ok: true });
  });
});

describe("401 刷新流程（不许被这次改动碰坏）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    localStorage.clear();
  });

  it("刷新成功 → 用新令牌重试一次，返回重试的结果", async () => {
    localStorage.setItem(ACCESS_KEY, "stale-access");
    localStorage.setItem(REFRESH_KEY, "refresh-token");

    const fetchMock = vi
      .fn()
      // 1) 原请求：401
      .mockResolvedValueOnce(response(JSON.stringify({ detail: "expired" }), { status: 401 }))
      // 2) 刷新：成功
      .mockResolvedValueOnce(
        response(JSON.stringify({ access: "fresh-access" }), { status: 200 })
      )
      // 3) 重试：成功
      .mockResolvedValueOnce(response(JSON.stringify({ switches: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const { regimeApi } = await import("@/lib/api");

    await expect(regimeApi.getOverview()).resolves.toEqual({ switches: [] });

    // 刷新那一次打到 refresh 端点
    expect(String(fetchMock.mock.calls[1][0])).toContain("/api/auth/token/refresh/");
    // 重试带上了**新**令牌——这是刷新这段代码唯一的目的
    const retryHeaders = fetchMock.mock.calls[2][1].headers as Record<string, string>;
    expect(retryHeaders.Authorization).toBe("Bearer fresh-access");
    expect(localStorage.getItem(ACCESS_KEY)).toBe("fresh-access");
  });

  it("刷新失败 → 清令牌、跳登录，抛的仍是 ApiError(401)", async () => {
    localStorage.setItem(ACCESS_KEY, "stale-access");
    localStorage.setItem(REFRESH_KEY, "refresh-token");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(JSON.stringify({ detail: "expired" }), { status: 401 }))
      .mockResolvedValueOnce(response(JSON.stringify({ detail: "bad" }), { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const { regimeApi, ApiError } = await import("@/lib/api");

    const failure = await regimeApi.getOverview().catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("Unauthorized");
    expect(failure.status).toBe(401);
    expect(localStorage.getItem(ACCESS_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_KEY)).toBeNull();
  });

  it("重试也失败 → 抛 ApiError 带重试那次的 status，不是 401", async () => {
    localStorage.setItem(ACCESS_KEY, "stale-access");
    localStorage.setItem(REFRESH_KEY, "refresh-token");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(JSON.stringify({ detail: "expired" }), { status: 401 }))
      .mockResolvedValueOnce(
        response(JSON.stringify({ access: "fresh-access" }), { status: 200 })
      )
      .mockResolvedValueOnce(
        response(JSON.stringify({ error: "还是不行" }), { status: 400 })
      );
    vi.stubGlobal("fetch", fetchMock);

    const { regimeApi, ApiError } = await import("@/lib/api");

    const failure = await regimeApi.getOverview().catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("还是不行");
    expect(failure.status).toBe(400);
  });
});

/**
 * 令牌刷新必须是**单飞**的，且「刷不出来」与「被限流」是两件事。
 *
 * ## 这一份钉的是什么缺陷（txy 生产实测，2026-11-05 16:41–16:55 CST）
 *
 * 那一刻后端日志里 `/api/...` 每分钟 50–128 条 `401 Unauthorized`，其中
 * `Unauthorized: /api/trading/klines/` 在 16:42 / 16:44 / 16:45 / 16:47 各一发——
 * 用户看到的就是「K 线一直加载不出来」。
 *
 * 根因不在 K 线接口本身（24 个 symbol×interval 组合逐个 curl 全是 200）：交易页一次
 * 挂载会同时发 10 个请求（positions / accounts / orders / frame-status / sessions /
 * halt / overview / agent-list / summary / me）。访问令牌过期时这 10 个**各自**走
 * `tryRefreshToken()`，于是**一次页面挂载打 10 发刷新**（浏览器实测 refresh 状态码
 * 十连 200）。`/api/auth/token/refresh/` 是 `AllowAny`，落在 `AnonRateThrottle`
 * 的 20/min 上——两次页面挂载就把匿名桶打穿，随后的刷新拿到 429。
 *
 * 而 429 在旧代码里与「刷新令牌真的过期」走同一条路（`!res.ok → false`）：
 * `clearTokens()` + 跳登录。**一次本该自愈的瞬时限流，把已登录用户踢下线**，
 * 之后的每一次请求（包括那发 K 线）都只剩 401。
 *
 * 所以两条契约：
 *   1. 并发 401 共用**同一次**刷新（单飞）。否则线程数 = 刷新数 = 限流自杀。
 *   2. 刷新被限流 / 5xx / 网络类失败 → **不清令牌、不跳登录**，把错误如实抛出去。
 *      只有刷新端点明确回 401（刷新令牌本身无效）才让用户重新登录。
 */
describe("令牌刷新：单飞 + 区分「刷不出来」与「被限流」", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    localStorage.clear();
  });

  it("10 个请求同时 401 → 只打 1 发刷新，不是 10 发", async () => {
    localStorage.setItem(ACCESS_KEY, "stale-access");
    localStorage.setItem(REFRESH_KEY, "refresh-token");

    let refreshCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/api/auth/token/refresh/")) {
        refreshCalls += 1;
        // 让刷新「慢一点」，把并发窗口撑开：否则单飞可能靠微任务顺序蒙对
        await new Promise((r) => setTimeout(r, 5));
        return response(JSON.stringify({ access: "fresh-access" }), { status: 200 });
      }
      // 带**旧**令牌 → 401；带刷新后的新令牌 → 200。这才是一次真实的过期挂载。
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      if (auth === "Bearer fresh-access") {
        return response(JSON.stringify({ switches: [] }), { status: 200 });
      }
      return response(JSON.stringify({ detail: "expired" }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = await import("@/lib/api");
    // 交易页一次挂载的 10 个并发请求的等价物
    const calls = await Promise.all([
      api.regimeApi.getOverview(),
      api.regimeApi.getHaltState(),
      api.tradingApi.getOrders(),
      api.tradingApi.getPositions(),
      api.tradingApi.getSummary(),
      api.tradingApi.getAccounts(),
      api.authApi.me(),
      api.marketApi.ticker({ source: "binance", symbol: "BTC/USDT" }),
      api.tradingApi.getKlines("BTC/USDT", "1h", 500),
      api.notificationApi.unreadCount(),
    ]);

    expect(calls).toHaveLength(10);
    expect(refreshCalls).toBe(1);
    expect(localStorage.getItem(ACCESS_KEY)).toBe("fresh-access");
  });

  it("刷新被限流（429）→ 不清令牌、不跳登录，错误如实抛出", async () => {
    localStorage.setItem(ACCESS_KEY, "stale-access");
    localStorage.setItem(REFRESH_KEY, "refresh-token");

    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/auth/token/refresh/")) {
        return response(JSON.stringify({ detail: "请求过于频繁" }), { status: 429 });
      }
      return response(JSON.stringify({ detail: "expired" }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = await import("@/lib/api");
    const failure = await api.tradingApi.getKlines("BTC/USDT", "1h", 500).catch((e) => e);

    expect(failure).toBeInstanceOf(Error);
    // 会话还在：限流是瞬时的，下一次 401 还能靠同一个刷新令牌自愈
    expect(localStorage.getItem(ACCESS_KEY)).toBe("stale-access");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("refresh-token");
  });

  it("刷新端点明确回 401（刷新令牌失效）→ 才清令牌、跳登录", async () => {
    localStorage.setItem(ACCESS_KEY, "stale-access");
    localStorage.setItem(REFRESH_KEY, "refresh-token");

    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/auth/token/refresh/")) {
        return response(JSON.stringify({ detail: "Token is invalid" }), { status: 401 });
      }
      return response(JSON.stringify({ detail: "expired" }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = await import("@/lib/api");
    const failure = await api.tradingApi.getKlines("BTC/USDT", "1h", 500).catch((e) => e);

    expect(failure).toBeInstanceOf(api.ApiError);
    expect(failure.message).toBe("Unauthorized");
    expect(failure.status).toBe(401);
    expect(localStorage.getItem(ACCESS_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_KEY)).toBeNull();
  });
});

describe("preferencesApi（顶栏行情币种偏好）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    localStorage.clear();
  });

  it("读偏好走 GET，并把后端回来的边界原样带出", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        JSON.stringify({
          header_symbols: ["BTC/USDT", "ETH/USDT", "DOGE/USDT"],
          is_default: false,
          min: 3,
          max: 5,
          default: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const { preferencesApi } = await import("@/lib/api");
    const pref = await preferencesApi.headerSymbols();

    // `request()` 会把尾部斜杠去掉（DRF 路由仍认），所以断言不带尾斜杠的前缀
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/auth/preferences");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
    expect(pref.header_symbols).toEqual(["BTC/USDT", "ETH/USDT", "DOGE/USDT"]);
    expect(pref.is_default).toBe(false);
    // 边界是后端的硬门槛，前端只信它回的值，不自己写死一份
    expect(pref.min).toBe(3);
    expect(pref.max).toBe(5);
  });

  it("保存偏好走 PUT，body 是整份替换而不是增量", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        JSON.stringify({
          header_symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
          is_default: false,
          min: 3,
          max: 5,
          default: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const { preferencesApi } = await import("@/lib/api");
    await preferencesApi.saveHeaderSymbols(["BTC/USDT", "ETH/USDT", "SOL/USDT"]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/auth/preferences");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({
      header_symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    });
  });

  it("后端 400 那句话原样进 ApiError.message（含条数范围）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(
          JSON.stringify({
            error: "顶栏行情币种至少 3 个，最多 5 个（当前 2 个）",
          }),
          { status: 400 }
        )
      )
    );

    const { preferencesApi, ApiError } = await import("@/lib/api");
    const failure = await preferencesApi
      .saveHeaderSymbols(["BTC/USDT", "ETH/USDT"])
      .catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("顶栏行情币种至少 3 个，最多 5 个（当前 2 个）");
    expect(failure.status).toBe(400);
  });
});
