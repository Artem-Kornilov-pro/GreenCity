// Аккаунты: логин/пароль, без подтверждения почты (backend/auth.py,
// backend/projects.py). Гостевой режим — это просто отсутствие сессии:
// редактор (загрузка DXF, генерация, правка текстом) им не интересуется,
// им пользуются только запросы к /api/projects (см. api.ts).
//
// Access + refresh, а не один токен: access живёт недолго (см.
// ACCESS_TOKEN_LIFETIME_MS, держится в паре с backend/auth.py::
// ACCESS_TOKEN_TTL_SECONDS) и идёт в заголовке каждого запроса; в
// localStorage переживает перезагрузку страницы только refresh-токен —
// именно по нему при необходимости молча получается новый access, не
// заставляя вводить пароль заново. Сам access-токен и его срок жизни живут
// только в памяти вкладки (см. cachedAccess) — независимо от того, что
// хранится в React-состоянии сессии, поэтому вызывающему коду не нужно
// самому заботиться о его свежести.

const REFRESH_KEY = "greencity_refresh_token";
const USERNAME_KEY = "greencity_username";
const ACCESS_TOKEN_LIFETIME_MS = 15 * 60 * 1000;

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

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

async function readErrorDetail(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : res.statusText;
}

async function requestNewAccessToken(refreshToken: string): Promise<string | null> {
  const res = await fetch(`${API_BASE}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) return null;
  const body = await res.json();
  return body.access_token as string;
}

// Действительный access-токен для запроса — обновляет его через refresh,
// только если истёк или скоро истечёт (5 c запаса на сам запрос). Не
// привязан к тому, какой Session-объект держит React в состоянии: два
// вызова подряд с разными (но одинаковыми по refreshToken) объектами Session
// не будут дважды обновлять токен впустую.
export async function getAccessToken(refreshToken: string): Promise<string | null> {
  if (cachedAccess && cachedAccess.refreshToken === refreshToken && cachedAccess.expiresAt > Date.now() + 5000) {
    return cachedAccess.token;
  }
  const token = await requestNewAccessToken(refreshToken);
  if (token) cachedAccess = { token, refreshToken, expiresAt: Date.now() + ACCESS_TOKEN_LIFETIME_MS };
  return token;
}

export async function authHeader(session: Session): Promise<Record<string, string>> {
  const token = await getAccessToken(session.refreshToken);
  if (!token) throw new Error("Сессия истекла, войдите заново");
  return { Authorization: `Bearer ${token}` };
}

async function authRequest(path: string, username: string, password: string): Promise<Session> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(await readErrorDetail(res));
  const body = await res.json();
  const session: Session = { refreshToken: body.refresh_token, username: body.username };
  saveSession(session, body.access_token);
  return session;
}

export function register(username: string, password: string): Promise<Session> {
  return authRequest("/api/auth/register", username, password);
}

export function login(username: string, password: string): Promise<Session> {
  return authRequest("/api/auth/login", username, password);
}

// Проверить, что сохранённый refresh-токен ещё действителен (не истёк за
// 30 дней, не отозван через logout, пользователь не удалён) — вызывается
// один раз при загрузке страницы.
export async function whoAmI(refreshToken: string): Promise<string | null> {
  const token = await getAccessToken(refreshToken);
  if (!token) return null;
  const res = await fetch(`${API_BASE}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) return null;
  const body = await res.json();
  return body.username as string;
}

// Отозвать refresh-токен на сервере (Redis) — без этого выход был бы только
// локальным удалением токенов, а сам refresh-токен оставался бы действителен
// ещё до 30 дней. Не бросает исключение при сбое сети — локальный выход
// (clearSession) не должен зависеть от того, доехал ли запрос до сервера.
export async function logout(refreshToken: string): Promise<void> {
  await fetch(`${API_BASE}/api/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }).catch(() => {});
}
