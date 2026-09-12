"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { useAuth } from "@/context/AuthContext";
import Topbar from "@/components/layout/Topbar";
import Sidebar from "@/components/layout/Sidebar";
import RightPanel from "@/components/layout/RightPanel";
import ChatWindow from "@/components/chat/ChatWindow";

interface RightPanelContextType {
  show: boolean;
  toggle: () => void;
}

export const RightPanelContext = createContext<RightPanelContextType>({
  show: false,
  toggle: () => {},
});

export function useRightPanel() {
  return useContext(RightPanelContext);
}

export default function DashboardShell({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const [showRightPanel, setShowRightPanel] = useState(true);

  if (isLoading) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-bg">
        <div className="text-text2 text-sm">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <RightPanelContext.Provider value={{ show: showRightPanel, toggle: () => setShowRightPanel((prev) => !prev) }}>
      <div
        className="h-screen w-screen grid grid-rows-[52px_1fr]"
        style={{ gridTemplateColumns: showRightPanel ? "220px 1fr 280px" : "220px 1fr 0px" }}
      >
        {/* Topbar spans full width */}
        <div style={{ gridColumn: "1 / -1" }}>
          <Topbar />
        </div>

        {/* Sidebar */}
        <div className="overflow-y-auto" style={{ gridRow: "2", gridColumn: "1" }}>
          <Sidebar />
        </div>

        {/* Main content */}
        {/* [&>*]:shrink-0: 滚动容器的子项不许被 flex 压缩，否则内容超出视口时
            子项(带 overflow-hidden 时 min-height 自动为 0)会被压扁裁切，容器无法滚动 */}
        <div className="overflow-y-auto pt-3 pb-16 px-4 flex flex-col gap-3 [&>*]:shrink-0" style={{ gridRow: "2", gridColumn: "2" }}>
          {children}
        </div>

        {/* Right panel */}
        <div className="overflow-hidden" style={{ gridRow: "2", gridColumn: "3" }}>
          {showRightPanel && <RightPanel />}
        </div>

        {/* Chat floating window */}
        <ChatWindow />
      </div>
    </RightPanelContext.Provider>
  );
}
