/**
 * Auth-контекст: сессия, логин/логаут, магазины.
 * Восстанавливает сессию при старте (кука уже может быть валидной).
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  SessionInfo,
  apiLogin,
  apiLogout,
  apiSession,
  apiSetActiveShop,
  loadBaseUrl,
  setUnauthorizedHandler,
} from "@/lib/api";

interface AuthState {
  ready: boolean;
  session: SessionInfo | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setActiveShop: (shopId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<SessionInfo | null>(null);

  useEffect(() => {
    // Сессия протухла на сервере → любой запрос вернёт 401 → на логин.
    setUnauthorizedHandler(() => setSession(null));
    (async () => {
      await loadBaseUrl();
      try {
        const s = await apiSession();
        setSession(s);
      } catch {
        setSession(null);
      } finally {
        setReady(true);
      }
    })();
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const s = await apiLogin(email, password);
    setSession(s);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch (e) {
      // Сессия могла уже истечь — локальный выход в любом случае.
      if (!(e instanceof ApiError)) throw e;
    }
    setSession(null);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await apiSession();
      setSession(s);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setSession(null);
    }
  }, []);

  const setActiveShop = useCallback(async (shopId: string) => {
    await apiSetActiveShop(shopId);
    setSession((prev) => (prev ? { ...prev, active_shop_id: shopId } : prev));
  }, []);

  const value = useMemo(
    () => ({ ready, session, login, logout, setActiveShop, refresh }),
    [ready, session, login, logout, setActiveShop, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
