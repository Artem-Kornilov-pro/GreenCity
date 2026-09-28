// Общая обёртка над fetch для всех запросов к API: адрес бэкенда, тело JSON
// или FormData, токен, разбор ошибок FastAPI и повтор при временных сбоях.

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  // HTTP-статус; 0 -- до сервера не достучались (сеть, CORS, сервер лежит).
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  json?: unknown;
  form?: FormData;
  query?: Record<string, string>;
  // Начало сообщения об ошибке: «Не удалось сохранить проект» ->
  // «Не удалось сохранить проект (500): <detail>». Без него -- только detail
  // (так, как его написал бэкенд: «Неверное имя пользователя или пароль»).
  errorMessage?: string;
  // Повторы при сетевой ошибке и 502/503/504. По умолчанию -- только у GET:
  // тяжёлые POST (GreenPlan, конвертация DWG, запрос к LLM) повторять
  // молча нельзя -- их повторяет пользователь кнопкой «Повторить».
  retries?: number;
  // Токен для запроса. force=true -- сервер ответил 401: токен надо
  // обновить, а не брать из кеша.
  authorize?: (force: boolean) => Promise<string>;
}

const RETRY_STATUSES = new Set([502, 503, 504]);
const RETRY_DELAYS_MS = [500, 1500, 3000];

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function readDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    // Ошибка валидации FastAPI: [{loc, msg, type}, ...]
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ");
    }
  } catch {
    // не JSON -- показываем текст как есть
  }
  return text || res.statusText;
}

function withPrefix(prefix: string | undefined, status: number, detail: string): string {
  return prefix ? `${prefix} (${status}): ${detail}` : detail;
}

// Ответ с 2xx; иначе -- ApiError с понятным текстом.
export async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const method = options.method ?? (options.json !== undefined || options.form ? "POST" : "GET");
  const retries = options.retries ?? (method === "GET" ? 2 : 0);
  const url = `${API_BASE}${path}${options.query ? `?${new URLSearchParams(options.query)}` : ""}`;
  const body = options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined);

  const attempt = async (forceToken: boolean): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (options.json !== undefined) headers["Content-Type"] = "application/json";
    if (options.authorize) headers.Authorization = `Bearer ${await options.authorize(forceToken)}`;
    return fetch(url, { method, headers, body });
  };

  for (let i = 0; ; i++) {
    let res: Response;
    try {
      res = await attempt(false);
      if (res.status === 401 && options.authorize) res = await attempt(true);
    } catch (e) {
      if (e instanceof ApiError) throw e; // authorize: сессия истекла
      if (i < retries) {
        await sleep(RETRY_DELAYS_MS[Math.min(i, RETRY_DELAYS_MS.length - 1)]);
        continue;
      }
      throw new ApiError(`${options.errorMessage ?? "Нет связи с сервером"}: сервер недоступен, проверьте подключение`, 0);
    }
    if (res.ok) return res;
    if (RETRY_STATUSES.has(res.status) && i < retries) {
      await sleep(RETRY_DELAYS_MS[Math.min(i, RETRY_DELAYS_MS.length - 1)]);
      continue;
    }
    throw new ApiError(withPrefix(options.errorMessage, res.status, await readDetail(res)), res.status);
  }
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const res = await send(path, options);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export async function requestBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  return (await send(path, options)).blob();
}
