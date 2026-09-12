import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { clearSession, loadSession, login as apiLogin, logout as apiLogout, register as apiRegister, whoAmI, type Session } from "../auth";
import { AuthContext } from "./auth-context-store";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [initializing, setInitializing] = useState(true);

  useEffect(() => {
    const run = () => {
      const saved = loadSession();
      if (!saved) {
        setInitializing(false);
        return;
      }
      whoAmI(saved.refreshToken)
        .then((username) => {
          if (username) {
            setSession(saved);
          } else {
            clearSession();
          }
        })
        .finally(() => setInitializing(false));
    };
    run();
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await apiLogin(username, password);
    setSession(result);
  }, []);

  const register = useCallback(async (username: string, password: string) => {
    const result = await apiRegister(username, password);
    setSession(result);
  }, []);

  const logout = useCallback(() => {
    if (session) apiLogout(session.refreshToken); // отозвать сессию на сервере — не дожидаемся ответа
    clearSession();
    setSession(null);
  }, [session]);

  const value = useMemo(() => ({ session, initializing, login, register, logout }), [session, initializing, login, register, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
