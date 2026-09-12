import type { NextConfig } from "next";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  (process.env.NODE_ENV === "development"
    ? "http://localhost:8000"
    : "http://backend:8000");

const nextConfig: NextConfig = {
  async rewrites() {
    // beforeFiles: 必须先于 Next 的 trailing-slash 归一化执行，
    // 否则带斜杠 URL 会被 Next 308 吃掉、永远到不了后端。
    // dev: /api 同域代理到本地 Django 后端(任意局域网机器同域,无 CORS)。
    // 注意:next dev 不代理 WebSocket upgrade,dev 下 WS 直连后端(见客户端 NEXT_PUBLIC_WS_URL 兜底)。
    return {
      beforeFiles: [
        { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*/` },
        { source: "/ws/:path*", destination: `${BACKEND_URL}/ws/:path*` },
      ],
    };
  },
};

export default nextConfig;
