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
    const apiBase =
      process.env.NEXT_PUBLIC_WS_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
    const httpBase = apiBase
      ? apiBase.replace(/^https?/, "ws")
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
    const url = new URL("/ws/logs/", httpBase);
    const token = getAccessToken();
    if (token) {
      url.searchParams.set("token", token);
    }
    return url.toString();
  }

  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;

  // 应用层心跳：浏览器休眠 / 网络切换 / 对端进程被杀这类「非干净关闭」
  // 不会触发 onclose，socket 会一直算在服务端 fd 里直到 TCP 超时（可能
  // 十几分钟）。定时 ping、超时无 pong 就主动断开，让回收及时发生。
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  // 发出 ping 但还没收到 pong 的时刻（null = 本轮已收到回复）。
  // 用时间戳而不是 setTimeout 判死：后台标签页的定时器会被浏览器节流
  // （可能一分钟才跑一次），独立 setTimeout 要么被压后、要么被 clamp，
  // 用它判死会误杀健康连接。按墙钟比较 elapsed 则不受节流影响。
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

  /** 主动丢弃一条已半死的连接：摘事件、关闭、留待重连。 */
  private _dropDeadConnection(reason: string) {
    console.warn(`[LogWebSocket] dropping dead connection: ${reason}`);
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
        // 上一轮的 pong 还没回来：超过容忍窗口就判定连接已半死。
        // 不主动断开的话，这条 socket 会一直算在服务端 fd 里，
        // 直到 TCP 层自己超时（可能十几分钟）。
        const waited = Date.now() - this.awaitingPongSince;
        if (waited > LogWebSocket.PONG_TIMEOUT_MS) {
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
    }, LogWebSocket.PING_INTERVAL_MS);
  }

  /** 收到 pong：本轮心跳得到确认。 */
  private _clearPongDeadline() {
    this.awaitingPongSince = null;
  }

  connect() {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING ||
        this.ws.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    // 旧实例可能是 CLOSING/CLOSED 的僵尸连接。直接覆盖 this.ws 会让它
    // 迟到的 onclose 仍然执行 reconnectAttempts++ 并再排一个重连，
    // 把计数推向封顶后页面就「开着但永远不刷新」。先摘事件再关掉。
    if (this.ws) {
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

    this.ws = new WebSocket(this.getWsUrl());

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this._startHeartbeat();
      if (this.currentParams) {
        this.subscribe(this.currentParams);
      }
    };

    this.ws.onmessage = (event) => {
      try {
        const data: WSMessage = JSON.parse(event.data);
        // pong 属于心跳控制帧，不投给业务回调（否则日志页会收到一堆
        // 类型不认识的帧）。收到即认为连接活着。
        if ((data as { type?: string }).type === "pong") {
          this._clearPongDeadline();
          return;
        }
        this._clearPongDeadline();
        this.callbacks.forEach((cb) => cb(data));
      } catch {
        // ignore non-JSON messages
      }
    };

    this.ws.onclose = () => {
      this._stopHeartbeat();
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
    this._stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    // 重置重连计数：现在离开日志页会真的断连，若不重置，用户重进页面时
    // 计数还停在上一轮的封顶值（10），首次断线就直接放弃重连 —— 日志
    // 页面会「打开着但永远不刷新」。
    this.reconnectAttempts = 0;
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

  /**
   * 取消服务端订阅但保留连接。
   * 服务端收到 unsubscribe 会停掉 Redis 读流并归还连接池；
   * 只在前端清回调、不发这条消息的话，后端那条 WS 连接与其
   * Redis 连接会一直算在进程的 nofile 里。
   */
  unsubscribe() {
    this.currentParams = null;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "unsubscribe" }));
    }
  }

  /** 当前还有几个回调订阅者（用于判断最后一个使用者离开时是否断开连接）。 */
  get listenerCount(): number {
    return this.callbacks.size;
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
