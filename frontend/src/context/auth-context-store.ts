import { createContext } from "react";
import type { Session } from "../auth";

export interface AuthContextValue {
  session: Session | null;
  // Пока не завершилась проверка сохранённого refresh-токена при старте --
  // страницы, которым нужна сессия, не должны успеть редиректнуть гостя на
  // /login только потому, что React ещё не досчитал whoAmI().
  initializing: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
