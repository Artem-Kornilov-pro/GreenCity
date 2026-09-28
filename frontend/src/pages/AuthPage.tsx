import { Link, Navigate, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { Leaf, ArrowLeft } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { PasswordInput } from "../components/ui/password-input";
import { PageTransition } from "../components/PageTransition";
import { loginSchema, registerSchema, type AuthFormValues } from "../lib/authSchema";
import { login, register as registerAccount, selectSession } from "../store/authSlice";
import { useAppDispatch, useAppSelector } from "../store/hooks";

function FieldHint({ id, error, hint }: { id: string; error?: string; hint?: string }) {
  if (error) {
    return (
      <p id={id} className="text-xs text-danger-500">
        {error}
      </p>
    );
  }
  return hint ? (
    <p id={id} className="text-xs text-ink-400">
      {hint}
    </p>
  ) : null;
}

export default function AuthPage({ mode }: { mode: "login" | "register" }) {
  const isLogin = mode === "login";
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  const location = useLocation();
  // Проверка -- при уходе с поля, дальше -- на каждый ввод; при отправке --
  // все поля сразу. Правила -- lib/authSchema.ts.
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<AuthFormValues>({
    resolver: zodResolver(isLogin ? loginSchema : registerSchema),
    mode: "onTouched",
    defaultValues: { username: "", password: "" },
  });

  if (session) {
    // Уже вошли (или только что вошли) -- туда, откуда пришли (например по
    // прямой ссылке на /login), а по умолчанию в проекты.
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? "/projects"} replace />;
  }

  // Ответ сервера («неверный пароль», «имя занято») -- не ошибка поля, а
  // ошибка формы целиком.
  const onSubmit = handleSubmit(async (values) => {
    const result = isLogin ? await dispatch(login(values)) : await dispatch(registerAccount(values));
    if (result.meta.requestStatus === "rejected") {
      setError("root.server", { message: (result.payload as string | undefined) ?? "Не удалось связаться с сервером" });
    }
  });

  return (
    <PageTransition>
      <div className="flex min-h-screen flex-col bg-ink-50">
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
              <p className="text-balance text-sm text-ink-500">
                {isLogin ? "Войдите, чтобы открыть сохранённые проекты" : "Только имя пользователя и пароль — почта не нужна"}
              </p>
            </div>

            <form onSubmit={onSubmit} noValidate className="mt-6 flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="username" className="text-sm font-medium text-ink-700">
                  Имя пользователя
                </label>
                <Input
                  id="username"
                  disabled={isSubmitting}
                  autoFocus
                  autoComplete="username"
                  aria-invalid={Boolean(errors.username)}
                  aria-describedby="username-hint"
                  className={errors.username ? "border-danger-500 focus:ring-danger-500/40" : undefined}
                  {...register("username")}
                />
                <FieldHint
                  id="username-hint"
                  error={errors.username?.message}
                  hint={isLogin ? undefined : "3–32 символа: латинские буквы, цифры, «_» и «.»"}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <label htmlFor="password" className="text-sm font-medium text-ink-700">
                  Пароль
                </label>
                <PasswordInput
                  id="password"
                  disabled={isSubmitting}
                  autoComplete={isLogin ? "current-password" : "new-password"}
                  aria-invalid={Boolean(errors.password)}
                  aria-describedby="password-hint"
                  className={errors.password ? "border-danger-500 focus:ring-danger-500/40" : undefined}
                  {...register("password")}
                />
                <FieldHint id="password-hint" error={errors.password?.message} hint={isLogin ? undefined : "Не короче 6 символов"} />
              </div>

              {errors.root?.server && (
                <p role="alert" className="rounded-lg bg-danger-500/10 px-3 py-2 text-sm text-danger-500">
                  {errors.root.server.message}
                </p>
              )}
              <Button type="submit" size="lg" disabled={isSubmitting} className="mt-1">
                {isSubmitting ? "Секунду…" : isLogin ? "Войти" : "Создать аккаунт"}
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
