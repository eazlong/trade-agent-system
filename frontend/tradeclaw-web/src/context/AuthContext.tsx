"use client";

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { useRouter, usePathname } from "next/navigation";
import { authApi, setTokens, clearTokens, getAccessToken } from "@/lib/api";

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: { username: string; email: string } | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState<{ username: string; email: string } | null>(null);
  const router = useRouter();
  const pathname = usePathname();

  // Restore session on mount
  useEffect(() => {
    const token = getAccessToken();
    if (token) {
      authApi
        .me()
        .then((userData) => {
          setUser({ username: userData.username, email: userData.email });
          setIsAuthenticated(true);
        })
        .catch(() => {
          clearTokens();
        })
        .finally(() => {
          setIsLoading(false);
        });
    } else {
      setIsLoading(false);
    }
  }, []);

  // Redirect if not authenticated
  useEffect(() => {
    if (isLoading) return;
    if (!isAuthenticated && pathname !== "/login" && pathname !== "/") {
      router.push("/login");
    }
  }, [isAuthenticated, isLoading, pathname, router]);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const data = await authApi.login({ email, password });
      setTokens(data.access, data.refresh);
      setUser({ username: data.user.username, email: data.user.email });
      setIsAuthenticated(true);
      router.push("/overview");
      return true;
    } catch {
      return false;
    }
  }, [router]);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore logout errors
    }
    clearTokens();
    setIsAuthenticated(false);
    setUser(null);
    router.push("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ isAuthenticated, isLoading, user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
