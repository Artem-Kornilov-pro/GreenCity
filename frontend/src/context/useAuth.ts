import { useContext } from "react";
import { AuthContext, type AuthContextValue } from "./auth-context-store";

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth должен вызываться внутри AuthProvider");
  return ctx;
}
