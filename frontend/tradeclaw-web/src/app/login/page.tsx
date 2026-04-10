"use client";

import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import Link from "next/link";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError("请输入邮箱和密码");
      return;
    }
    setLoading(true);
    setError("");
    const success = await login(email, password);
    setLoading(false);
    if (!success) {
      setError("邮箱或密码错误");
    }
  };

  return (
    <div className="h-screen w-screen bg-bg flex items-center justify-center">
      {/* Background effect */}
      <div className="fixed inset-0" style={{ background: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px)" }} />

      <div className="relative bg-bg1 border border-[rgba(255,255,255,0.12)] rounded-xl p-8 w-[420px] max-w-[90vw]">
        {/* Logo */}
        <div className="flex items-center gap-3 mb-8 justify-center">
          <div className="w-10 h-10 bg-gradient-to-br from-green to-teal rounded-lg flex items-center justify-center">
            <svg viewBox="0 0 16 16" fill="none" className="w-5 h-5">
              <path d="M8 1L14 4.5V11.5L8 15L2 11.5V4.5L8 1Z" stroke="#000" strokeWidth="1.5" />
              <circle cx="8" cy="8" r="2" fill="#000" />
            </svg>
          </div>
          <div>
            <div className="text-lg font-bold tracking-wide">TradeClaw</div>
            <div className="text-[10px] font-mono text-text3 uppercase tracking-widest">Agent OS · v2.4.1</div>
          </div>
        </div>

        <h2 className="text-base font-bold mb-6 text-center">登录系统</h2>

        {error && (
          <div className="mb-4 px-3 py-2 bg-red-dim border border-red/20 rounded-lg text-xs text-red text-center">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1.5">邮箱</label>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2.5 text-xs text-text outline-none focus:border-green transition-colors"
              placeholder="输入邮箱"
            />
          </div>

          <div className="mb-6">
            <label className="block text-xs font-semibold text-text2 uppercase tracking-wider mb-1.5">密码</label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2.5 text-xs text-text outline-none focus:border-green transition-colors"
              placeholder="输入密码"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg text-xs font-semibold bg-green text-black hover:opacity-85 transition-all cursor-pointer disabled:opacity-50"
          >
            {loading ? "登录中..." : "登录"}
          </button>
        </form>

        <div className="mt-4 text-center text-xs text-text3">
          需要账号？请联系管理员注册
        </div>
      </div>
    </div>
  );
}
