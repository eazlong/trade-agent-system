import { getAccessToken } from "./api";

export type LogEntry = {
  type: "log_entry";
  id: string;
  level: string;
  module: string;
  message: string;
  trace_id: string;
  extra_data: Record<string, unknown>;
  created_at: string;
};

type SubscribedMessage = {
  type: "subscribed";
  level: string;
  module: string | null;
};

type ErrorMessage = {
  type: "error";
  error: string;
};

type WSMessage = LogEntry | SubscribedMessage | ErrorMessage;

export type LogSubscribeParams = {
  level?: string;
  module?: string;
  search?: string;
};

type Callback = (message: WSMessage) => void;

class LogWebSocket {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private callbacks: Set<Callback> = new Set();
  private currentParams: LogSubscribeParams | null = null;

  private getWsUrl(): string {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const httpBase = apiBase.replace(/^https?/, "ws");
    const url = new URL("/ws/logs/", httpBase);
    const token = getAccessToken();
    if (token) {
      url.searchParams.set("token", token);
    }
    return url.toString();
  }

  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;

  connect() {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING ||
        this.ws.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    this.ws = new WebSocket(this.getWsUrl());

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      if (this.currentParams) {
        this.subscribe(this.currentParams);
      }
    };

    this.ws.onmessage = (event) => {
      try {
        const data: WSMessage = JSON.parse(event.data);
        this.callbacks.forEach((cb) => cb(data));
      } catch {
        // ignore non-JSON messages
      }
    };

    this.ws.onclose = () => {
      this.reconnectAttempts++;
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        console.warn("[LogWebSocket] Max reconnect attempts reached");
        return;
      }
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
    };

    this.ws.onerror = () => {
      // onclose fires after onerror, so reconnect is handled there
    };
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null; // prevent reconnect
      this.ws.close();
      this.ws = null;
    }
  }

  subscribe(params: LogSubscribeParams) {
    this.currentParams = params;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "subscribe", ...params }));
    }
  }

  onMessage(callback: Callback) {
    this.callbacks.add(callback);
    return () => this.callbacks.delete(callback);
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export const logWS = new LogWebSocket();
