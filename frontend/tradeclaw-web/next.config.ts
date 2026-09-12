import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // dev 模式:/api 同域代理到本地 Django 后端,
  // 使浏览器可经 http://192.168.10.24:3000/api 访问(任意局域网机器均同域,无 CORS 问题)。
  // 注意:next dev 不代理 WebSocket upgrade,WS 直连后端(见 NEXT_PUBLIC_WS_URL)
  rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
    ];
  },
};

export default nextConfig;
