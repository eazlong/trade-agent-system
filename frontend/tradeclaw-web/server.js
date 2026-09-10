const http = require("http");
const path = require("path");
const httpProxy = require("http-proxy");

const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";
const PORT = 3000;

// 后端 (FastAPI) 的规范路径都带尾斜杠；补上斜杠可省掉一次 301 跳转
function forceTrailingSlash(req) {
  const q = req.url.indexOf("?");
  const p = q === -1 ? req.url : req.url.slice(0, q);
  const qs = q === -1 ? "" : req.url.slice(q);
  if (p.length > 1 && !p.endsWith("/")) req.url = p + "/" + qs;
}

const proxy = httpProxy.createProxyServer({
  target: BACKEND_URL,
  changeOrigin: true,
});

proxy.on("error", (err, req, res) => {
  console.error("[proxy] error for " + req.url + ": " + err.message);
  if (res && !res.headersSent && typeof res.writeHead === "function") {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end('{"detail":"backend proxy error"}');
  }
});

const next = require("next");
const app = next({ dev: process.env.NODE_ENV !== "production", dir: __dirname });
const handle = app.getRequestHandler();

app.prepare().then(() => {
  const server = http.createServer((req, res) => {
    if (req.url === "/api" || req.url.startsWith("/api/") || req.url === "/ws" || req.url.startsWith("/ws/")) {
      forceTrailingSlash(req);
      proxy.web(req, res);
      return;
    }
    handle(req, res);
  });

  // WebSocket 升级：/ws/* 直接透传给后端（Next 生产服务器不支持 rewrite 代理 upgrade）
  server.on("upgrade", (req, socket, head) => {
    if (req.url === "/ws" || req.url.startsWith("/ws/")) {
      forceTrailingSlash(req);
      proxy.ws(req, socket, head);
    }
  });

  server.listen(PORT, "0.0.0.0", () => {
    console.log("\u2713 Custom server on :" + PORT + " (proxy \u2192 " + BACKEND_URL + ")");
  });
});
