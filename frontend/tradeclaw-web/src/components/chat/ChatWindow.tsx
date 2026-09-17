"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { chatWS, type ChatMessageWS, type StatusMessage } from "@/lib/chatWs";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  status: "sending" | "done" | "error";
  task_id?: string;
}

// JSON 检测：仅接受整体可解析的对象/数组（字符串、数字等标量不渲染）。
export function parseJsonPayload(text: string): unknown | null {
  const t = text.trim();
  if (!t.startsWith("{") && !t.startsWith("[")) return null;
  try {
    const v: unknown = JSON.parse(t);
    return v !== null && typeof v === "object" ? v : null;
  } catch {
    return null;
  }
}

/** 消息气泡内容：JSON 对象/数组渲染为紧凑语法高亮视图，其余按纯文本。 */
export function MessageContent({ content }: { content: string }) {
  const json = parseJsonPayload(content);
  if (json !== null) {
    const obj: Record<string, unknown> =
      json !== null && typeof json === "object" && !Array.isArray(json) ? (json as Record<string, unknown>) : {};
    const rows = Object.entries(obj);
    const isArr = Array.isArray(json);
    const list = isArr ? (json as unknown[]) : rows;
    const showList = rows.length > 4 || rows.length === 0;
    return (
      <div className="max-h-72 overflow-auto my-1 rounded-md bg-black/30 border border-[rgba(255,255,255,0.08)]">
        <div className="sticky top-0 bg-bg3/95 backdrop-blur px-2 py-1 text-[9px] font-mono text-green border-b border-[rgba(255,255,255,0.07)] flex justify-between">
          <span>JSON</span>
          <span className="text-text3">{isArr ? `${(json as unknown[]).length} items` : `${rows.length} fields`}</span>
        </div>
        <div className="px-2 py-1.5">
          <table className="w-full border-collapse font-mono text-[10.5px] leading-relaxed">
            <tbody>
              {showList
                ? list.map((row, i) => {
                    const [k, v]: [string, unknown] = isArr
                      ? [String(i), row as unknown]
                      : (row as [string, unknown]);
                    return (
                      <tr key={k} className="align-top">
                        <td className="pr-2 py-[1px] text-[#7dd3fc] whitespace-nowrap">{k}</td>
                        <td className="py-[1px] text-text2 break-all">{renderJsonValue(v)}</td>
                      </tr>
                    );
                  })
                : rows.map(([k, v]) => (
                    <tr key={k} className="align-top">
                      <td className="pr-2 py-[1px] text-[#7dd3fc] whitespace-nowrap">{k}</td>
                      <td className="py-[1px] text-text2 break-all">{renderJsonValue(v)}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  return <div className="break-words whitespace-pre-wrap">{content}</div>;
}

function renderJsonValue(v: unknown) {
  if (v === null) return <span className="text-text3 italic">null</span>;
  if (typeof v === "number") return <span className="text-[#fbbf24]">{String(v)}</span>;
  if (typeof v === "boolean") return <span className="text-red">{String(v)}</span>;
  if (typeof v === "string") {
    return v.length > 120 ? (
      <span className="text-[#a5d6a7]">{v.slice(0, 120)}…</span>
    ) : (
      <span className="text-[#a5d6a7]">{v}</span>
    );
  }
  if (Array.isArray(v)) {
    if (v.length > 3) {
      return <span className="text-text3">[{v.length} items] {v.slice(0, 3).map((x) => renderScalar(x)).join(", ")}…</span>;
    }
    return <span className="text-text2">[{v.map((x) => renderScalar(x)).join(", ")}]</span>;
  }
  if (typeof v === "object") {
    const s = JSON.stringify(v);
    return <span className="text-[#c4b5fd]">{s && s.length > 80 ? s.slice(0, 80) + "…" : s}</span>;
  }
  return String(v);
}

function renderScalar(x: unknown) {
  if (x === null) return "null";
  if (typeof x === "string") return x.length > 40 ? x.slice(0, 40) + "…" : x;
  if (typeof x === "object") return JSON.stringify(x);
  return String(x);
}

const STORAGE_KEY = "tradeclaw_chat_history";

function loadHistory(): ChatMessage[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

function saveHistory(messages: ChatMessage[]) {
  if (typeof window === "undefined") return;
  try {
    // Keep last 100 messages to avoid storage bloat
    const trimmed = messages.slice(-100);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // storage full or disabled
  }
}

export default function ChatWindow() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>(loadHistory);
  const [input, setInput] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [size, setSize] = useState({ w: 760, h: 1040 });
  const resizeRef = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const MIN_W = 320;
  const MIN_H = 400;
  const MAX_W = 1600;
  const MAX_H = 1800;

  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: size.w, startH: size.h };

    const onMouseMove = (ev: MouseEvent) => {
      if (!resizeRef.current) return;
      const dx = ev.clientX - resizeRef.current.startX;
      const dy = ev.clientY - resizeRef.current.startY;
      setSize({
        w: Math.min(MAX_W, Math.max(MIN_W, resizeRef.current.startW - dx)),
        h: Math.min(MAX_H, Math.max(MIN_H, resizeRef.current.startH - dy)),
      });
    };

    const onMouseUp = () => {
      resizeRef.current = null;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };

  // Persist messages to localStorage
  useEffect(() => {
    saveHistory(messages);
  }, [messages]);

  // WebSocket connection management
  useEffect(() => {
    // Keep receiving notifications even when the chat panel is collapsed.
    const unsub = chatWS.onMessage((msg) => {
      if (msg.type === "status") {
        const statusMsg = msg as StatusMessage;
        if (statusMsg.status === "connected") {
          setWsConnected(true);
          // 连接（重）建立：若仍有 "sending" 占位（断线前发出、后端可能
          // 通过 ws_pending 补投的回复），不能盲目复位 isProcessing，
          // 否则会提前解除发送锁定并让占位永远转圈（"不响应"）。
          setMessages((prev) => {
            const hasPending = prev.some(
              (m) => m.role === "assistant" && m.status === "sending"
            );
            if (!hasPending) setIsProcessing(false);
            return prev;
          });
        } else if (statusMsg.status === "processing") {
          setIsProcessing(true);
        }
      } else if (msg.type === "chat_response") {
        const chatMsg = msg as ChatMessageWS;
        setIsProcessing(false);

        setMessages((prev) => {
          const updated = [...prev];
          // 找到最后一个 sending 状态的 assistant 占位
          const lastSendingIdx = updated.findLastIndex(
            (m) => m.role === "assistant" && m.status === "sending"
          );
          const resolved: ChatMessage = (() => {
            if (chatMsg.status === "done") {
              const rawData = chatMsg.data;
              const contentStr =
                typeof rawData === "string"
                  ? rawData
                  : rawData && typeof rawData === "object" && "content" in rawData
                    ? String(rawData.content)
                    : rawData != null
                      ? JSON.stringify(rawData)
                      : "";
              return {
                id: chatMsg.task_id || `msg-${Date.now()}`,
                role: "assistant",
                content: contentStr,
                timestamp: new Date().toISOString(),
                status: "done",
                task_id: chatMsg.task_id,
              };
            }
            return {
              id: chatMsg.task_id || `msg-${Date.now()}`,
              role: "assistant",
              content: chatMsg.error || "未知错误",
              timestamp: new Date().toISOString(),
              status: "error",
              task_id: chatMsg.task_id,
            };
          })();

          if (lastSendingIdx !== -1) {
            updated[lastSendingIdx] = resolved;
          } else {
            // 没有匹配的占位（如重连后占位已被清理/多条消息交错）——
            // 绝不能静默丢弃响应，否则用户看到的就是"不响应"。
            updated.push(resolved);
          }
          return updated;
        });
      } else if (msg.type === "task_notification" || msg.type === "task_submitted" || msg.type === "tool_progress") {
        const content = msg.type === "task_notification" ? msg.data
          : msg.type === "task_submitted" ? `任务已提交：${msg.task_id}`
          : `[${msg.tool}] ${msg.result}`;
        const id = msg.delivery_id || `notice-${Date.now()}-${Math.random()}`;
        setMessages((prev) => prev.some((m) => m.id === id) ? prev : [...prev, {
          id, role: "assistant", content, timestamp: new Date().toISOString(), status: "done",
        }]);
      } else if (msg.type === "error") {
        setIsProcessing(false);
        setMessages((prev) => {
          const updated = [...prev];
          const lastSendingIdx = updated.findLastIndex(
            (m) => m.role === "assistant" && m.status === "sending"
          );
          if (lastSendingIdx !== -1) {
            updated[lastSendingIdx] = {
              ...updated[lastSendingIdx],
              content: msg.error || "未知错误",
              status: "error",
            };
          } else {
            // 同理：没有占位时也把错误展示出来，而不是丢掉
            updated.push({
              id: `err-${Date.now()}`,
              role: "assistant",
              content: msg.error || "未知错误",
              timestamp: new Date().toISOString(),
              status: "error",
            });
          }
          return updated;
        });
      }
      // "pong" messages are silently ignored
    });

    chatWS.connect(); // Subscribe first so replay cannot arrive without a listener.
    return () => {
      unsub();
      chatWS.disconnect();
    };
  }, []);

  // Auto-scroll to bottom — 每次打开面板时也滚动到底部
  useEffect(() => {
    if (isOpen) {
      // 使用小延迟确保 DOM 已渲染
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      });
    }
  }, [messages, isProcessing, isOpen]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height =
        Math.min(textareaRef.current.scrollHeight, 96) + "px";
    }
  }, [input]);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || isProcessing) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
      status: "done",
    };
    const assistantPlaceholder: ChatMessage = {
      id: `assistant-${Date.now()}`,
      role: "assistant",
      content: "",
      timestamp: new Date().toISOString(),
      status: "sending",
    };

    setMessages((prev) => [...prev, userMsg, assistantPlaceholder]);
    setInput("");
    setIsProcessing(true);
    chatWS.send(text);
  }, [input, isProcessing]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = () => {
    setMessages([]);
    if (typeof window !== "undefined") {
      localStorage.removeItem(STORAGE_KEY);
    }
  };

  const formatTime = (ts: string) => {
    const d = new Date(ts);
    return d.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  return (
    <>
      {/* Floating Button */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-6 w-12 h-12 bg-gradient-to-br from-green to-teal rounded-full shadow-lg hover:opacity-85 transition-all cursor-pointer z-50 flex items-center justify-center"
          title="打开 Supervisor 对话"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M3 5C3 3.89543 3.89543 3 5 3H15C16.1046 3 17 3.89543 17 5V12C17 13.1046 16.1046 14 15 14H7L3 18V5Z"
              stroke="#000"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
            <circle cx="7" cy="8" r="1" fill="#000" />
            <circle cx="10" cy="8" r="1" fill="#000" />
            <circle cx="13" cy="8" r="1" fill="#000" />
          </svg>
        </button>
      )}

      {/* Chat Panel */}
      {isOpen && (
        <div
          className="fixed bottom-6 right-6 bg-bg1 border border-[rgba(255,255,255,0.12)] rounded-xl shadow-2xl z-50 flex flex-col overflow-hidden"
          style={{ width: size.w, height: size.h, boxShadow: "0 8px 32px rgba(0,0,0,0.4)" }}
        >
          {/* Header */}
          <div className="px-4 py-3 border-b border-[rgba(255,255,255,0.07)] flex items-center justify-between bg-bg2">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-green animate-pulse-slow" style={{ boxShadow: "0 0 6px var(--color-green)" }} />
              <span className="text-xs font-semibold">Supervisor 对话</span>
              <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded ${wsConnected ? "bg-green-dim text-green" : "bg-bg3 text-text3"}`}>
                {wsConnected ? "已连接" : "连接中"}
              </span>
            </div>
            <div className="flex items-center gap-1">
              {messages.length > 0 && (
                <button
                  onClick={handleClear}
                  className="p-1 rounded text-text3 hover:text-text transition-all cursor-pointer"
                  title="清空对话"
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M2 3H10L9 11H3L2 3Z" stroke="currentColor" strokeWidth="1" />
                    <path d="M1 3H11" stroke="currentColor" strokeWidth="1" />
                    <path d="M4 1V3" stroke="currentColor" strokeWidth="1" />
                    <path d="M8 1V3" stroke="currentColor" strokeWidth="1" />
                  </svg>
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 rounded text-text3 hover:text-text transition-all cursor-pointer"
                title="最小化"
              >
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <line x1="2" y1="6" x2="10" y2="6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                </svg>
              </button>
            </div>
          </div>

          {/* Messages Area */}
          <div className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-2.5">
            {messages.length === 0 && (
              <div className="flex items-center justify-center h-full text-text3 text-xs">
                <div className="text-center">
                  <div className="mb-2">与 SupervisorAgent 对话</div>
                  <div className="text-[10px] text-text3">
                    发送消息进行意图识别、策略分析或自由对话
                  </div>
                </div>
              </div>
            )}

            {messages.map((msg) => (
              <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] px-3.5 py-2 rounded-xl text-xs ${
                    msg.role === "user"
                      ? "bg-green-dim border border-green/20 text-text rounded-tr-sm"
                      : msg.status === "error"
                        ? "bg-red-dim border border-red/20 text-text rounded-tl-sm"
                        : "bg-bg3 border border-[rgba(255,255,255,0.07)] text-text2 rounded-tl-sm"
                  }`}
                >
                  {msg.status === "sending" ? (
                    <span className="flex items-center gap-1">
                      <span className="animate-blink-dots">.</span>
                      <span className="animate-blink-dots" style={{ animationDelay: "0.2s" }}>.</span>
                      <span className="animate-blink-dots" style={{ animationDelay: "0.4s" }}>.</span>
                    </span>
                  ) : (
                    <>
                      <MessageContent content={msg.content} />
                      <div className="text-[9px] text-text3 font-mono mt-1 text-right">
                        {formatTime(msg.timestamp)}
                      </div>
                    </>
                  )}
                </div>
              </div>
            ))}

            {isProcessing && messages[messages.length - 1]?.status !== "sending" && (
              <div className="flex justify-start">
                <div className="bg-bg3 border border-[rgba(255,255,255,0.07)] px-3.5 py-2 rounded-xl rounded-tl-sm">
                  <span className="flex items-center gap-1 text-text3">
                    <span className="animate-blink-dots">.</span>
                    <span className="animate-blink-dots" style={{ animationDelay: "0.2s" }}>.</span>
                    <span className="animate-blink-dots" style={{ animationDelay: "0.4s" }}>.</span>
                  </span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Area */}
          <div className="px-3 py-2.5 border-t border-[rgba(255,255,255,0.07)] bg-bg2">
            <div className="flex items-end gap-2">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入消息... (Enter 发送)"
                rows={1}
                className="flex-1 bg-bg3 border border-[rgba(255,255,255,0.07)] rounded-lg px-3 py-2 text-xs text-text placeholder-text3 resize-none outline-none focus:border-green/30 transition-all"
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isProcessing}
                className={`p-2 rounded-lg cursor-pointer transition-all mb-0.5 ${
                  input.trim() && !isProcessing
                    ? "bg-green text-black hover:opacity-85"
                    : "bg-bg3 text-text3 cursor-not-allowed"
                }`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M1 7L12 1L8 7L12 13L1 7Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                </svg>
              </button>
            </div>
          </div>
          {/* Resize Handle */}
          <div
            onMouseDown={handleResizeMouseDown}
            className="absolute top-0 left-0 w-4 h-4 cursor-nwse-resize flex items-start justify-start p-0.5 z-10"
            style={{ touchAction: "none" }}
          >
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none" className="opacity-30">
              <path d="M1 7V1H7" stroke="var(--color-text3)" strokeWidth="1" strokeLinecap="round" />
              <path d="M1 4V1H4" stroke="var(--color-text3)" strokeWidth="1" strokeLinecap="round" />
            </svg>
          </div>
        </div>
      )}
    </>
  );
}
