import { authHeader, type Session } from "./auth";
import type { Scene } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

async function readErrorDetail(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : res.statusText;
}

export async function uploadDxf(file: File): Promise<Scene> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/parse`, { method: "POST", body: form });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Ошибка парсинга (${res.status}): ${text || res.statusText}`);
  }
  return res.json() as Promise<Scene>;
}

// Бэкенд-эндпоинт сейчас заглушка (backend/main.py::generate_greenery) --
// возвращает null, пока алгоритм не реализован. null здесь не ошибка сети,
// поэтому не бросаем исключение, а даём вызывающему коду решить, что делать
// (см. App.tsx -- сцену в этом случае не трогаем и показываем сообщение).
export async function generateGreenery(scene: Scene): Promise<Scene | null> {
  const res = await fetch(`${API_BASE}/api/generate-greenery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scene),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Не удалось сгенерировать растительность (${res.status}): ${text || res.statusText}`);
  }
  return res.json() as Promise<Scene | null>;
}

// Итоговый план -> файл .dxf (backend/export_dxf.py; ТЗ: "итоговый план
// должен экспортироваться обратно в формат DXF"). Возвращаем Blob, а не сами
// триггерим скачивание -- так функцию можно переиспользовать (например для
// предпросмотра), а вызывающий код (App.tsx) сам решает, что делать дальше.
export async function exportDxf(scene: Scene): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/export-dxf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scene),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Не удалось экспортировать DXF (${res.status}): ${text || res.statusText}`);
  }
  return res.blob();
}

export interface TextEditResult {
  scene: Scene;
  explanation: string;
  applied: string[];
  rejected: string[];
  warnings: string[];
}

// Правка плана текстом через LLM (backend/llm_editor.py). Модель отвечает
// несколько секунд -- вызывающему коду нужен индикатор ожидания.
export async function editWithText(scene: Scene, instruction: string): Promise<TextEditResult> {
  const res = await fetch(`${API_BASE}/api/edit-with-text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene, instruction }),
  });
  if (!res.ok) {
    // FastAPI кладёт текст ошибки в {"detail": "..."} -- показываем его, а не
    // сырое тело ответа.
    throw new Error(`Не удалось применить правку (${res.status}): ${await readErrorDetail(res)}`);
  }
  return res.json() as Promise<TextEditResult>;
}

// --- Проекты (backend/projects.py) -------------------------------------------
//
// Требуют вход в аккаунт -- гостевой режим их не касается: редактор выше
// работает без сессии совсем. authHeader (auth.ts) сам получает свежий
// access-токен (обновляя его через refresh-токен сессии, если истёк), так
// что каждая функция здесь просто передаёт текущую сессию, а не голый токен.
// Не больше трёх проектов на пользователя -- лимит проверяет бэкенд, здесь
// только прокидывается его сообщение об ошибке.

export interface ProjectSummary {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface Project extends ProjectSummary {
  scene: Scene;
}

export async function listProjects(session: Session): Promise<ProjectSummary[]> {
  const res = await fetch(`${API_BASE}/api/projects`, { headers: await authHeader(session) });
  if (!res.ok) throw new Error(`Не удалось получить список проектов (${res.status}): ${await readErrorDetail(res)}`);
  return res.json() as Promise<ProjectSummary[]>;
}

export async function createProject(session: Session, name: string, scene: Scene): Promise<Project> {
  const res = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeader(session)) },
    body: JSON.stringify({ name, scene }),
  });
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json() as Promise<Project>;
}

export async function loadProject(session: Session, id: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`, { headers: await authHeader(session) });
  if (!res.ok) throw new Error(`Не удалось открыть проект (${res.status}): ${await readErrorDetail(res)}`);
  return res.json() as Promise<Project>;
}

export async function saveProject(session: Session, id: string, scene: Scene): Promise<Project> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...(await authHeader(session)) },
    body: JSON.stringify({ scene }),
  });
  if (!res.ok) throw new Error(`Не удалось сохранить проект (${res.status}): ${await readErrorDetail(res)}`);
  return res.json() as Promise<Project>;
}

export async function renameProject(session: Session, id: string, name: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...(await authHeader(session)) },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Не удалось переименовать проект (${res.status}): ${await readErrorDetail(res)}`);
  return res.json() as Promise<Project>;
}

export async function deleteProject(session: Session, id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`, { method: "DELETE", headers: await authHeader(session) });
  if (!res.ok) throw new Error(`Не удалось удалить проект (${res.status}): ${await readErrorDetail(res)}`);
}
