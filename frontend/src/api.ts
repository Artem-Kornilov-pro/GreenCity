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

// Папка проекта из .dwg (issue #50) -- backend конвертирует каждый файл через
// dwg2dxf/LibreDWG и сливает их в один документ (dwg_batch_converter.py)
// прежде чем прогнать через тот же parser/parse_dxf.py, что и /api/parse.
// Файлы, которые не удалось сконвертировать, не считаются ошибкой всего
// запроса -- попадают в Scene.dwgConversionWarnings.
export async function uploadDwgFolder(files: File[]): Promise<Scene> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const res = await fetch(`${API_BASE}/api/parse-dwg`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Ошибка конвертации DWG (${res.status}): ${await readErrorDetail(res)}`);
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

// Итоговый план -> файл .dxf (backend/exchange/export_dxf.py; ТЗ: "итоговый план
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

// Пояснительная записка GreenPlan в DOCX (backend/greenplan/document.py).
// scene -- ТЕКУЩАЯ сцена редактора (с правками после GreenPlan), нарушения и
// ведомость бэкенд пересчитывает по ней сам; report -- уже полученный текст
// из /api/greenplan/report, заново LLM не вызывается.
export async function downloadGreenPlanDocument(
  scene: Scene,
  assignments: GreenPlanZoneAssignment[],
  report: string | null,
  title: string | null,
  options: GreenPlanOptions | null = null,
  notes: string[] = []
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/greenplan/document`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene, assignments, report, title, options, notes }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Не удалось сформировать пояснительную записку (${res.status}): ${text || res.statusText}`);
  }
  return res.blob();
}

// GreenPlan -- автоозеленение по прошлым проектам (backend: pattern_assignment.py,
// violation_report.py, assortment_report.py, decision_report.py). Решения
// (assignments) считаются полностью детерминированно, без LLM, и быстро
// (find_violations -- через пространственный индекс, доли секунды даже на
// крупных участках). Текст-объяснение (report) -- через локальную LLM
// (mistral:7b/Ollama), занимает ~30 секунд, поэтому отдельный запрос
// (/api/greenplan/report), а не часть /api/greenplan/generate -- иначе
// пользователь ждал бы уже готовую расстановку все эти 30 секунд ради
// текста, который к ней не относится. Недоступность Ollama -- не ошибка
// запроса (report_error заполнен, report null), сама расстановка не страдает.
export interface GreenPlanZoneAssignment {
  zone_id: string;
  zone_kind: string;
  pattern_id: string;
  source_project: string | null;
  source_quote: string | null;
  confidence: number;
  // Подобранные виды и основание подбора (backend/greenplan/species_selection.py).
  tree_species?: string[];
  bush_species?: string[];
  species_basis?: string | null;
  // Общее решение на участок (backend/greenplan/pattern_assignment.py), у
  // всех зон одинаковое: стиль и ведущий проект-аналог.
  site_style?: "regular" | "landscape" | null;
  lead_project?: string | null;
}

export interface GreenPlanViolation {
  object_id: string;
  object_type: string;
  zone_id: string;
  zone_type: string;
  severity: string;
  distance_m: number;
  required_m: number;
  message: string;
}

export interface GreenPlanAssortmentRow {
  category: string;
  species: string;
  count: number;
  // "шт." у деревьев и кустарников, "м²" у газона.
  unit: string;
}

export interface GreenPlanGenerateResult {
  scene: Scene;
  assignments: GreenPlanZoneAssignment[];
  violations: GreenPlanViolation[];
  assortment: GreenPlanAssortmentRow[];
  // Благоустройство: новые дорожки (м²), фонари, скамейки, урны.
  improvements: GreenPlanAssortmentRow[];
  // Что из параметров не удалось выполнить и почему.
  notes: string[];
}

// Параметры GreenPlan (backend/greenplan/options.py) -- диалог перед запуском.
export interface GreenPlanOptions {
  style: "auto" | "regular" | "landscape";
  trees: boolean;
  bushes: boolean;
  lawn: boolean;
  preferred_trees: string[];
  preferred_bushes: string[];
  paths: boolean;
  lighting: boolean;
  benches: boolean;
}

export const DEFAULT_GREENPLAN_OPTIONS: GreenPlanOptions = {
  style: "auto",
  trees: true,
  bushes: true,
  lawn: true,
  preferred_trees: [],
  preferred_bushes: [],
  paths: false,
  lighting: false,
  benches: false,
};

export async function generateGreenPlan(scene: Scene, options: GreenPlanOptions = DEFAULT_GREENPLAN_OPTIONS): Promise<GreenPlanGenerateResult> {
  const res = await fetch(`${API_BASE}/api/greenplan/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene, options }),
  });
  if (!res.ok) {
    throw new Error(`Не удалось построить GreenPlan (${res.status}): ${await readErrorDetail(res)}`);
  }
  return res.json() as Promise<GreenPlanGenerateResult>;
}

export interface GreenPlanReportResult {
  report: string | null;
  report_error: string | null;
}

// Отдельный запрос, ~30 секунд (локальная LLM) -- вызывающий код (EditorPage)
// не ждёт его перед тем, как показать уже готовый результат generateGreenPlan.
export async function fetchGreenPlanReport(assignments: GreenPlanZoneAssignment[]): Promise<GreenPlanReportResult> {
  const res = await fetch(`${API_BASE}/api/greenplan/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(assignments),
  });
  if (!res.ok) {
    throw new Error(`Не удалось получить отчёт GreenPlan (${res.status}): ${await readErrorDetail(res)}`);
  }
  return res.json() as Promise<GreenPlanReportResult>;
}

export interface TextEditResult {
  scene: Scene;
  explanation: string;
  applied: string[];
  rejected: string[];
  warnings: string[];
  // Модель выбрала озеленение GreenPlan -- его запускает вызывающий код на scene.
  greenplan?: GreenPlanOptions | null;
}

// Прошлая правка чата (backend/text_editor/operations.py::ChatTurn): без неё
// модель не понимает отсылок вроде "убери их".
export interface ChatTurn {
  instruction: string;
  explanation: string;
  applied: string[];
}

// Правка плана текстом через LLM (backend/text_editor/service.py). Модель отвечает
// несколько секунд -- вызывающему коду нужен индикатор ожидания.
export async function editWithText(scene: Scene, instruction: string, history: ChatTurn[] = []): Promise<TextEditResult> {
  const res = await fetch(`${API_BASE}/api/edit-with-text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene, instruction, history }),
  });
  if (!res.ok) {
    // FastAPI кладёт текст ошибки в {"detail": "..."} -- показываем его, а не
    // сырое тело ответа.
    throw new Error(`Не удалось применить правку (${res.status}): ${await readErrorDetail(res)}`);
  }
  return res.json() as Promise<TextEditResult>;
}

// --- Проекты (backend/accounts/projects.py) -------------------------------------------
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
