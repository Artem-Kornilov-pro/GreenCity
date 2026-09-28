import { z } from "zod";

// Правила те же, что у бэкенда (backend/accounts/auth.py: USERNAME_RE,
// MIN_PASSWORD_LENGTH; schemas.py: длины). Здесь -- чтобы показать ошибку
// сразу у поля; сервер проверяет ещё раз и на случай обхода формы.

// Вход: только заполненность -- правила регистрации не подсказываем.
export const loginSchema = z.object({
  username: z.string().trim().min(1, "Введите имя пользователя"),
  password: z.string().min(1, "Введите пароль"),
});

export const registerSchema = z.object({
  username: z
    .string()
    .trim()
    .min(3, "Не короче 3 символов")
    .max(32, "Не длиннее 32 символов")
    .regex(/^[A-Za-z0-9_.]+$/, "Только латинские буквы, цифры, «_» и «.»"),
  password: z.string().min(6, "Не короче 6 символов").max(200, "Не длиннее 200 символов"),
});

export type AuthFormValues = z.infer<typeof loginSchema>;
