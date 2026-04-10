import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TradeClaw — 交易智能体操作系统",
  description: "AI Agent 驱动的量化交易辅助系统",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="h-full">{children}</body>
    </html>
  );
}
