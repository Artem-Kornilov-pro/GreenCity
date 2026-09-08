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
