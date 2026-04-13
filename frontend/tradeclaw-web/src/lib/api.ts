const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Token Management ──

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("tradeclaw_access");
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("tradeclaw_refresh");
}

export function setTokens(access: string, refresh: string) {
  localStorage.setItem("tradeclaw_access", access);
  localStorage.setItem("tradeclaw_refresh", refresh);
}

export function clearTokens() {
  localStorage.removeItem("tradeclaw_access");
  localStorage.removeItem("tradeclaw_refresh");
}

// ── Base Fetch ──

async function request<T>(
  path: string,
  method = "GET",
  body?: unknown
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getAccessToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    // Try refresh
    const refreshed = await tryRefreshToken();
    if (!refreshed) {
      clearTokens();
      window.location.href = "/login";
      throw new Error("Unauthorized");
    }
    // Retry with new token
    const newToken = getAccessToken();
    if (newToken) {
      headers.Authorization = `Bearer ${newToken}`;
    }
    const retryRes = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!retryRes.ok) {
      throw new Error(`API error: ${retryRes.status}`);
    }
    return retryRes.json() as Promise<T>;
  }

  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }

  return res.json() as Promise<T>;
}

async function tryRefreshToken(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  try {
    const res = await fetch(`${API_BASE}/api/auth/token/refresh/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    setTokens(data.access, refresh);
    return true;
  } catch {
    return false;
  }
}

// ── Auth API ──

export interface LoginPayload {
  email: string;
  password: string;
}

export interface LoginResponse {
  user: {
    id: number;
    email: string;
    username: string;
    is_active: boolean;
    is_frozen: boolean;
    created_at: string;
    last_login_at: string | null;
  };
  access: string;
  refresh: string;
}

export interface RegisterPayload {
  email: string;
  username: string;
  password: string;
}

export const authApi = {
  login: (payload: LoginPayload) =>
    request<LoginResponse>("/api/auth/login/", "POST", payload),

  register: (payload: RegisterPayload) =>
    request<LoginResponse>("/api/auth/register/", "POST", payload),

  me: () => request<LoginResponse["user"]>("/api/auth/me/"),

  logout: () => {
    const refresh = getRefreshToken();
    return request<Record<string, string>>("/api/auth/logout/", "POST", {
      refresh,
    });
  },
};

// ── Trading API ──

export interface Order {
  id: string;
  request_id: string;
  exchange_account: number;
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit" | "stop";
  quantity: string;
  price: string | null;
  status: "pending" | "submitted" | "partial" | "filled" | "cancelled" | "failed";
  exchange_order_id: string;
  filled_quantity: string;
  avg_fill_price: string | null;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface Strategy {
  id: string;
  name: string;
  code_path: string;
  git_commit_hash: string;
  is_active: boolean;
  created_at: string;
}

export const tradingApi = {
  getOrders: () => request<Order[]>("/api/trading/orders/"),
  getOrder: (id: string) => request<Order>(`/api/trading/orders/${id}/`),
  getStrategies: () => request<Strategy[]>("/api/trading/strategies/"),
  createStrategy: (data: Partial<Strategy>) =>
    request<Strategy>("/api/trading/strategies/", "POST", data),
  getSummary: () =>
    request<TradingSummary>("/api/trading/summary/"),
  getPositions: () =>
    request<PositionResponse>("/api/trading/positions/"),
  getAccounts: () =>
    request<ExchangeAccountWithBalance[]>("/api/trading/accounts/"),
};

// ── Trading Page Types ──

export interface PositionInfo {
  exchange: string;
  exchange_account_id?: string;
  symbol: string;
  side: "long" | "short";
  quantity: string;
  entry_price: string;
  mark_price: string;
  unrealized_pnl: string;
}

export interface PositionResponse {
  positions: PositionInfo[];
  executor_running: boolean;
  message?: string;
}

export interface TradingSummary {
  total_equity: string;
  today_realized_pnl: string;
  active_orders_count: number;
  total_orders_today: number;
  account_count: number;
}

export interface ExchangeAccountWithBalance {
  id: string;
  exchange: string;
  label: string;
  is_active: boolean;
  testnet: boolean;
  created_at: string;
  balance?: {
    total: string;
    available: string;
    used: string;
  };
}

// ── Risk API ──

export interface RiskEvent {
  id: string;
  level: "P0" | "P1" | "P2";
  event_type: "hard_limit" | "circuit_breaker" | "reconciliation" | "heartbeat";
  message: string;
  resolved: boolean;
  created_at: string;
  resolved_at: string | null;
}

export interface RiskConfig {
  daily_loss_warning_pct: number;
  consecutive_loss_alert: number;
  position_suggestion_limit: number;
  max_position_pct: number;
  max_drawdown_pct: number;
  var_limit_pct: number;
  stop_loss_pct: number;
  auto_stop: boolean;
  updated_at: string;
}

export const riskApi = {
  getEvents: () => request<RiskEvent[]>("/api/risk/events/"),
  getConfig: () => request<RiskConfig>("/api/risk/config/"),
  updateConfig: (data: Partial<RiskConfig>) =>
    request<RiskConfig>("/api/risk/config/", "PUT", data),
};

// ── Exchange API ──

export interface ExchangeAccount {
  id: string;
  exchange: string;
  label: string;
  is_active: boolean;
  testnet: boolean;
  created_at: string;
  balance?: {
    total: string;
    available: string;
    used: string;
  };
}

export interface CreateExchangeAccountPayload {
  exchange: string;
  label?: string;
  api_key: string;
  api_secret: string;
  testnet?: boolean;
  leverage?: number;
}

export const exchangeApi = {
  getAccounts: () => request<ExchangeAccount[]>("/api/exchange/accounts/"),
  createAccount: (data: CreateExchangeAccountPayload) =>
    request<ExchangeAccount>("/api/exchange/accounts/", "POST", data),
  deleteAccount: (id: string) =>
    request<void>(`/api/exchange/accounts/${id}/`, "DELETE"),
};

// ── Agent API ──

export const agentApi = {
  chat: (message: string, frameId?: string) =>
    request<Record<string, unknown>>("/api/agent/chat/", "POST", {
      message,
      frame_id: frameId,
    }),
  getFrameStatus: () =>
    request<Record<string, unknown>>("/api/agent/frame/status/"),
  controlFrame: (action: string, frameId?: string) =>
    request<Record<string, unknown>>("/api/agent/frame/control/", "POST", {
      action,
      frame_id: frameId,
    }),
};

// ── Logging API ──

export interface SystemLog {
  id: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  module: string;
  logger_name: string;
  message: string;
  trace_id: string;
  extra_data: Record<string, unknown>;
  created_at: string;
}

export interface LogQueryParams {
  level?: string;
  module?: string;
  search?: string;
  trace_id?: string;
  since?: string;
  before?: string;
}

export const loggingApi = {
  getLogs: (params?: LogQueryParams) => {
    const query = new URLSearchParams();
    if (params?.level) query.set("level", params.level);
    if (params?.module) query.set("module", params.module);
    if (params?.search) query.set("search", params.search);
    if (params?.trace_id) query.set("trace_id", params.trace_id);
    if (params?.since) query.set("since", params.since);
    if (params?.before) query.set("before", params.before);
    const qs = query.toString();
    return request<SystemLog[]>(`/api/logs/${qs ? `?${qs}` : ""}`);
  },
};

// ── Backtest API ──

export interface BacktestResult {
  id: string;
  strategy: number;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
  final_capital: string;
  total_return_pct: number;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  win_rate: number | null;
  total_trades: number;
  git_commit_hash: string;
  parameters: Record<string, unknown>;
  created_at: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
  drawdown: number;
}

export interface DrawdownPoint {
  timestamp: string;
  drawdown: number;
}

export interface OHLCVPoint {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorData {
  ma7?: number[];
  ma25?: number[];
  ma99?: number[];
  boll?: { upper: number[]; mid: number[]; lower: number[] };
  macd?: { dif: number[]; dea: number[]; hist: number[] };
  rsi?: number[];
}

export interface BacktestDetail extends BacktestResult {
  equity_curve: EquityPoint[];
  drawdown_curve: DrawdownPoint[];
  ohlcv_data: OHLCVPoint[];
  indicator_data: IndicatorData;
}

export interface BacktestTrade {
  id: string;
  entry_time: string;
  exit_time: string | null;
  symbol: string;
  side: "long" | "short";
  entry_price: string;
  exit_price: string | null;
  quantity: string;
  pnl: string | null;
  pnl_pct: number | null;
  cumulative_pnl: string | null;
  fees: string | null;
  tags: string[];
  created_at: string;
}

export interface PaginatedTrades {
  count: number;
  num_pages: number;
  current_page: number;
  results: BacktestTrade[];
}

export const backtestApi = {
  getList: () => request<BacktestResult[]>("/api/backtest/results/"),
  getDetail: (id: string) =>
    request<BacktestResult>(`/api/backtest/results/${id}/`),
  getFullDetail: (id: string) =>
    request<BacktestDetail>(`/api/backtest/results/${id}/detail/`),
  getTrades: (
    id: string,
    params?: { page?: number; page_size?: number; sort?: string }
  ) => {
    const query = new URLSearchParams();
    if (params?.page) query.set("page", String(params.page));
    if (params?.page_size) query.set("page_size", String(params.page_size));
    if (params?.sort) query.set("sort", params.sort);
    const qs = query.toString();
    return request<PaginatedTrades>(
      `/api/backtest/results/${id}/trades/${qs ? `?${qs}` : ""}`
    );
  },
};
