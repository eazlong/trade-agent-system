import { getAccessToken, tryRefreshToken, clearTokens } from "./api";

export type ChatMessageWS = {
  type: "chat_response";
  data?: string | Record<string, unknown>;
  error?: string;
  task_id?: string;
  status: "done" | "error";
};

type StatusMessage = {
  type: "status";
  status: "connected" | "processing";
};

export type { StatusMessage };

type PongMessage = {
  type: "pong";
};

type ErrorMessage = {
  type: "error";
  error: string;
};

type WSMessage = ChatMessageWS | StatusMessage | PongMessage | ErrorMessage;

type Callback = (message: WSMessage) => void;

class ChatWebSocket {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private callbacks: Set<Callback> = new Set();
  private pendingMessages: string[] = [];
  private reconnectAttempt = 0;
  private refreshing = false;
  // 握手是否曾成功过(open 过)。握手被拒时浏览器只能拿到 1006,用它区分
  // "连接建立后异常断开"(应重连) 与 "握手阶段被服务端拒绝"(应走鉴权修复)。
  private everOpened = false;
  // 连续"握手被拒"的修复轮数,封顶后降级为退避重连,避免 refresh 成功但
  // 用户已不存在等场景下无限刷新-重连打爆后端。
  private preOpenRejects = 0;

  private getWsUrl(): string {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const httpBase = apiBase.replace(/^https?/, "ws");
    const url = new URL("/ws/chat/", httpBase);
    const token = getAccessToken();
    if (token) {
      url.searchParams.set("token", token);
    }
    return url.toString();
  }

  connect() {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING ||
        this.ws.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    this.everOpened = false;
    this.ws = new WebSocket(this.getWsUrl());

    this.ws.onopen = () => {
      this.everOpened = true;
      this.preOpenRejects = 0;
      // 重连后发送 pending 消息
      if (this.pendingMessages.length > 0) {
        const pending = [...this.pendingMessages];
        this.pendingMessages = [];
        pending.forEach((text) => this._sendRaw(text));
      }
    };

    this.ws.onmessage = (event) => {
      let data: WSMessage;
      try {
        data = JSON.parse(event.data);
      } catch {
        // ignore non-JSON messages
        return;
      }
      // Dispatch outside the JSON try so a handler error is surfaced in the
      // console instead of being silently swallowed — otherwise a thrown
      // handler would drop the response with no trace ("不响应" with no clue).
      this.callbacks.forEach((cb) => {
        try {
          cb(data);
        } catch (err) {
          console.error("[chatWs] message handler error:", err);
        }
      });
    };

    this.ws.onclose = (ev) => {
      // 后端鉴权失败关闭（无 token / token 无效 / user inactive）→ 4001
      // 浏览器层 policy 拒绝 → 1008
      // 握手阶段被服务端拒绝时(HTTP 403),close code 不会传到浏览器,
      // 只能看到 1006 —— 一样是鉴权失败,纳入修复(刷新 token 或跳登录)。
      const handshakeRejected = !this.everOpened && ev.code === 1006;
      if (
        ev.code === 4001 ||
        ev.code === 1008 ||
        (handshakeRejected && this.preOpenRejects < 2)
      ) {
        if (handshakeRejected) {
          this.preOpenRejects++;
        }
        this._handleAuthFailure();
        return;
      }
      if (handshakeRejected) {
        // refresh 成功后仍被拒(如用户已不存在) —— 降级为退避重连,
        // 避免无限"刷新-重连"循环打爆后端。
        console.error("[chatWs] handshake rejected repeatedly, giving up auto-repair");
      }
      this._scheduleReconnect();
    };

    this.ws.onerror = () => {
      // onclose fires after onerror, reconnect handled there
    };
  }

  private _scheduleReconnect() {
    // 指数退避：3s → 6s → 12s → 24s → 48s → 60s（封顶），避免 auth 失败时刷后端日志
    this.reconnectAttempt++;
    const delay = Math.min(3000 * 2 ** (this.reconnectAttempt - 1), 60_000);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private async _handleAuthFailure() {
    // 同一 refresh 请求只跑一次，并发握手共享结果
    if (this.refreshing) {
      setTimeout(() => this._handleAuthFailure(), 500);
      return;
    }
    this.refreshing = true;
    try {
      const ok = await tryRefreshToken();
      this.refreshing = false;
      if (ok) {
        this.reconnectAttempt = 0;
        this.connect();
      } else {
        clearTokens();
        window.location.href = "/login";
      }
    } catch {
      this.refreshing = false;
      clearTokens();
      window.location.href = "/login";
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempt = 0;
    this.everOpened = false;
    this.preOpenRejects = 0;
    if (this.ws) {
      this.ws.onclose = null; // prevent reconnect
      this.ws.close();
      this.ws = null;
    }
  }

  private _sendRaw(text: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "chat", text }));
    }
  }

  send(text: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._sendRaw(text);
    } else {
      // 存储 pending，连接后自动发送
      this.pendingMessages.push(text);
      this.connect();
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

export const chatWS = new ChatWebSocket();
