import { getAccessToken } from "./api";

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

    this.ws = new WebSocket(this.getWsUrl());

    this.ws.onopen = () => {
      // 重连后发送 pending 消息
      if (this.pendingMessages.length > 0) {
        const pending = [...this.pendingMessages];
        this.pendingMessages = [];
        pending.forEach((text) => this._sendRaw(text));
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
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
    };

    this.ws.onerror = () => {
      // onclose fires after onerror, reconnect handled there
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
