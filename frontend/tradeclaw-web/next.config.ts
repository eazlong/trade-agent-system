import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    // beforeFiles: 必须先于 Next 的 trailing-slash 归一化执行，
    // 否则带斜杠 URL 会被 Next 308 吃掉、永远到不了后端。
    return {
      beforeFiles: [
        { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*/` },
        { source: "/ws/:path*", destination: `${BACKEND_URL}/ws/:path*/` },
      ],
    };
  },
};

export default nextConfig;
