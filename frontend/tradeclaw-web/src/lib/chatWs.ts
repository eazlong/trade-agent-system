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

type NotificationMessage = {
  type: "task_notification";
  data: string;
  delivery_id?: string;
};

type SubmittedMessage = { type: "task_submitted"; task_id: string };
type ToolProgressMessage = { type: "tool_progress"; tool: string; result: string };

type WSMessage = (ChatMessageWS | StatusMessage | PongMessage | ErrorMessage |
  NotificationMessage | SubmittedMessage | ToolProgressMessage) & { delivery_id?: string };

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

  // 应用层心跳。浏览器休眠 / 网络切换 / 对端进程被杀不会触发 onclose，
  // socket 会一直算在服务端 fd 里直到 TCP 超时（可能十几分钟）。后端
  // ChatConsumer 已支持 {"type":"ping"} → {"type":"pong"}。
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  // 发出 ping 但还没收到 pong 的时刻（null = 本轮已收到回复）。
  // 用墙钟时间戳而不是 setTimeout 判死：后台标签页的定时器会被浏览器
  // 节流（可能一分钟才跑一次），独立 setTimeout 要么被压后、要么被
  // clamp，用它判死会误杀健康连接。
  private awaitingPongSince: number | null = null;
  private static readonly PING_INTERVAL_MS = 25_000;
  private static readonly PONG_TIMEOUT_MS = 10_000;

  private _stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    this.awaitingPongSince = null;
  }

  private _clearPongDeadline() {
    this.awaitingPongSince = null;
  }

  /** 主动丢弃一条已半死的连接并重连。 */
  private _dropDeadConnection(reason: string) {
    console.warn(`[chatWs] dropping dead connection: ${reason}`);
    this._stopHeartbeat();
    const dead = this.ws;
    this.ws = null;
    if (dead) {
      dead.onclose = null;
      dead.onerror = null;
      dead.onmessage = null;
      try {
        dead.close();
      } catch {
        // 已关闭的实例 close() 可能抛，忽略
      }
    }
    this.connect();
  }

  private _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState !== WebSocket.OPEN) return;
      if (this.awaitingPongSince !== null) {
        const waited = Date.now() - this.awaitingPongSince;
        if (waited > ChatWebSocket.PONG_TIMEOUT_MS) {
          this._dropDeadConnection(`no pong in ${waited}ms`);
        }
        return;
      }
      try {
        this.ws.send(JSON.stringify({ type: "ping" }));
        this.awaitingPongSince = Date.now();
      } catch {
        // onclose 会接管重连
      }
    }, ChatWebSocket.PING_INTERVAL_MS);
  }

  private getWsUrl(): string {
    const apiBase =
      process.env.NEXT_PUBLIC_WS_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
    const httpBase = apiBase
      ? apiBase.replace(/^https?/, "ws")
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
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

    // 旧实例可能是 CLOSING 或已 CLOSED 但还没被 GC 掉的僵尸连接。
    // 直接覆盖 this.ws 会让它既不走 onclose、也不再被任何引用关闭，
    // 服务端那条 socket 就一直挂着 —— 挂机重连 N 次就多 N 条，
    // 最终把后端的 nofile 吃满（表现为所有请求都进不来）。
    if (this.ws) {
      // onmessage 一并摘掉：CLOSING 状态下旧连接可能还有已排队的帧，
      // 不清的话会把同一个 chat_response 再投一次给回调。
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      try {
        this.ws.close();
      } catch {
        // 已关闭的实例 close() 可能抛，忽略
      }
      this.ws = null;
    }

    this.everOpened = false;
    this.ws = new WebSocket(this.getWsUrl());

    this.ws.onopen = () => {
      this.everOpened = true;
      this.preOpenRejects = 0;
      this._startHeartbeat();
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
      // pong 是心跳控制帧：清判死计时器后直接返回，不投给业务回调
      // （ChatWindow 那边本来也只会静默忽略）。
      if ((data as { type?: string }).type === "pong") {
        this._clearPongDeadline();
        return;
      }
      this._clearPongDeadline();
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
      this._stopHeartbeat();
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
      const result = await tryRefreshToken();
      this.refreshing = false;
      if (result.ok) {
        this.reconnectAttempt = 0;
        this.connect();
      } else if (result.kind === "invalid") {
        // 刷新令牌真的失效了 —— 只有这一种情况需要重新登录
        clearTokens();
        window.location.href = "/login";
      } else {
        // 限流 / 5xx / 断网：会话还在，靠指数退避重连，别把用户踢下线
        this._scheduleReconnect();
      }
    } catch {
      this.refreshing = false;
      // 刷新通路本身出错 ≠ 令牌失效，同样走退避重连
      this._scheduleReconnect();
    }
  }

  disconnect() {
    this._stopHeartbeat();
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

  /**
   * 对带 delivery_id 的消息回送达确认。
   * 服务端收到 ack 后才从离线缓冲删除对应条目；未 ack 前重连会补发。
   */
  ackDelivery(deliveryId: string) {
    if (!deliveryId || this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "delivery_ack", delivery_id: deliveryId }));
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
