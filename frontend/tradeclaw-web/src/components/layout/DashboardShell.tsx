"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { useAuth } from "@/context/AuthContext";
import Topbar from "@/components/layout/Topbar";
import Sidebar from "@/components/layout/Sidebar";
import RightPanel from "@/components/layout/RightPanel";

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
        <div className="overflow-y-auto py-3 px-4 flex flex-col gap-3" style={{ gridRow: "2", gridColumn: "2" }}>
          {children}
        </div>

        {/* Right panel */}
        <div className="overflow-hidden" style={{ gridRow: "2", gridColumn: "3" }}>
          {showRightPanel && <RightPanel />}
        </div>
      </div>
    </RightPanelContext.Provider>
  );
}
