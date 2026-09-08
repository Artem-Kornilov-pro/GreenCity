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
