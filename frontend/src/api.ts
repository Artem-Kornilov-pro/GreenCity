import type { Scene } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

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
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : res.statusText;
    throw new Error(`Не удалось применить правку (${res.status}): ${detail}`);
  }
  return res.json() as Promise<TextEditResult>;
}
