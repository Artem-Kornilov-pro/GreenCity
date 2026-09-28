// Аккаунты: имя и пароль, без почты. Гостевой режим -- просто отсутствие
// сессии; токен нужен только запросам к /api/projects.
//
// Access-токен короткоживущий и хранится только в памяти вкладки
// (cachedAccess); в localStorage переживает перезагрузку только
// refresh-токен, по которому access молча обновляется.

import { ApiError, request, send } from "./lib/http";

const REFRESH_KEY = "greencity_refresh_token";
const USERNAME_KEY = "greencity_username";
const ACCESS_TOKEN_LIFETIME_MS = 15 * 60 * 1000;

export interface Session {
  refreshToken: string;
  username: string;
}

let cachedAccess: { token: string; refreshToken: string; expiresAt: number } | null = null;

export function loadSession(): Session | null {
  const refreshToken = localStorage.getItem(REFRESH_KEY);
  const username = localStorage.getItem(USERNAME_KEY);
  return refreshToken && username ? { refreshToken, username } : null;
}

function saveSession(session: Session, accessToken: string): void {
  localStorage.setItem(REFRESH_KEY, session.refreshToken);
  localStorage.setItem(USERNAME_KEY, session.username);
  cachedAccess = { token: accessToken, refreshToken: session.refreshToken, expiresAt: Date.now() + ACCESS_TOKEN_LIFETIME_MS };
}

export function clearSession(): void {
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USERNAME_KEY);
  cachedAccess = null;
}

function isRejectedSession(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 401 || e.status === 403);
}

// Действительный access-токен: обновляется через refresh, только если истёк,
// истечёт в ближайшие 5 с или сервер его уже не принял (force). null --
// refresh-токен отозван или истёк; сбой сети -- исключение, сессия не теряется.
async function getAccessToken(refreshToken: string, force = false): Promise<string | null> {
  if (!force && cachedAccess && cachedAccess.refreshToken === refreshToken && cachedAccess.expiresAt > Date.now() + 5000) {
    return cachedAccess.token;
  }
  let body: { access_token: string };
  try {
    body = await request<{ access_token: string }>("/api/auth/refresh", { json: { refresh_token: refreshToken } });
  } catch (e) {
    if (isRejectedSession(e)) return null;
    throw e;
  }
  cachedAccess = { token: body.access_token, refreshToken, expiresAt: Date.now() + ACCESS_TOKEN_LIFETIME_MS };
  return body.access_token;
}

// Токен для запросов от имени сессии (RequestOptions.authorize).
export function sessionAuth(session: Session): (force: boolean) => Promise<string> {
  return async (force) => {
    const token = await getAccessToken(session.refreshToken, force);
    if (!token) throw new ApiError("Сессия истекла, войдите заново", 401);
    return token;
  };
}

async function authenticate(path: string, username: string, password: string): Promise<Session> {
  const body = await request<{ refresh_token: string; access_token: string; username: string }>(path, { json: { username, password } });
  const session: Session = { refreshToken: body.refresh_token, username: body.username };
  saveSession(session, body.access_token);
  return session;
}

export function register(username: string, password: string): Promise<Session> {
  return authenticate("/api/auth/register", username, password);
}

export function login(username: string, password: string): Promise<Session> {
  return authenticate("/api/auth/login", username, password);
}

// Проверить сохранённый refresh-токен (не истёк, не отозван) -- один раз
// при загрузке страницы. null -- сессию сервер не принимает; сбой сети --
// исключение (сессию из-за него не сбрасываем).
export async function whoAmI(session: Session): Promise<string | null> {
  try {
    const body = await request<{ username: string }>("/api/auth/me", { authorize: sessionAuth(session) });
    return body.username;
  } catch (e) {
    if (isRejectedSession(e)) return null;
    throw e;
  }
}

// Отозвать refresh-токен на сервере. Сбой сети не мешает локальному выходу.
export async function logout(refreshToken: string): Promise<void> {
  await send("/api/auth/logout", { json: { refresh_token: refreshToken } }).catch(() => {});
}
