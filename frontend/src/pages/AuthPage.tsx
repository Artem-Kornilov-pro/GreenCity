import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { Leaf, ArrowLeft } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { PageTransition } from "../components/PageTransition";
import { useAuth } from "../context/useAuth";

export default function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { session, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session) {
    // Уже вошли -- этой странице тут делать нечего, отправляем туда, откуда
    // пришли (например по прямой ссылке на /login), а по умолчанию в проекты.
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? "/projects"} replace />;
  }

  const isLogin = mode === "login";

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      if (isLogin) await login(username.trim(), password);
      else await register(username.trim(), password);
      navigate("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageTransition>
      <div className="flex min-h-full flex-col bg-ink-50">
        <div className="p-6">
          <Link to="/" className="inline-flex items-center gap-1.5 text-sm font-medium text-ink-500 transition-colors hover:text-ink-800">
            <ArrowLeft className="h-4 w-4" />
            На главную
          </Link>
        </div>

        <div className="flex flex-1 items-center justify-center px-6 pb-24">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
            className="w-full max-w-sm rounded-2xl border border-ink-200/70 bg-white p-8 shadow-soft-lg"
          >
            <div className="flex flex-col items-center gap-2 text-center">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-600 text-white">
                <Leaf className="h-5 w-5" />
              </span>
              <h1 className="mt-1 text-xl font-semibold text-ink-900">{isLogin ? "С возвращением" : "Создать аккаунт"}</h1>
              <p className="text-sm text-ink-500">
                {isLogin ? "Войдите, чтобы открыть сохранённые проекты" : "Только имя пользователя и пароль — почта не нужна"}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-3">
              <Input
                placeholder="Имя пользователя"
                value={username}
                disabled={busy}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
              />
              <Input
                type="password"
                placeholder="Пароль"
                value={password}
                disabled={busy}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isLogin ? "current-password" : "new-password"}
              />
              {error && <p className="rounded-lg bg-danger-500/10 px-3 py-2 text-sm text-danger-500">{error}</p>}
              <Button type="submit" size="lg" disabled={busy || !username.trim() || !password} className="mt-1">
                {busy ? "Секунду…" : isLogin ? "Войти" : "Создать аккаунт"}
              </Button>
            </form>

            <p className="mt-5 text-center text-sm text-ink-500">
              {isLogin ? (
                <>
                  Нет аккаунта?{" "}
                  <Link to="/register" className="font-medium text-brand-700 hover:underline">
                    Зарегистрируйтесь
                  </Link>
                </>
              ) : (
                <>
                  Уже есть аккаунт?{" "}
                  <Link to="/login" className="font-medium text-brand-700 hover:underline">
                    Войдите
                  </Link>
                </>
              )}
            </p>
          </motion.div>
        </div>
      </div>
    </PageTransition>
  );
}
