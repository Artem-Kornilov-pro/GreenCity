import { sessionAuth, type Session } from "./auth";
import { request, requestBlob, send } from "./lib/http";
import type { Scene } from "./types";

export function uploadDxf(file: File): Promise<Scene> {
  const form = new FormData();
  form.append("file", file);
  return request<Scene>("/api/parse", { form, errorMessage: "Ошибка парсинга" });
}

// Папка .dwg: бэкенд конвертирует файлы, склеивает в один документ и
// разбирает тем же парсером. Файлы, которые не удалось сконвертировать,
// перечислены в Scene.dwgConversionWarnings.
export function uploadDwgFolder(files: File[]): Promise<Scene> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return request<Scene>("/api/parse-dwg", { form, errorMessage: "Ошибка конвертации DWG" });
}

// Итоговый план -> .dxf. mode: "overlay" -- слои результата дописаны в
// исходный чертёж (исходные слои и координаты не тронуты), "rebuilt" --
// исходника на сервере нет, DXF собран из сцены.
export async function exportDxf(scene: Scene): Promise<{ blob: Blob; mode: "overlay" | "rebuilt" }> {
  const res = await send("/api/export-dxf", { json: scene, errorMessage: "Не удалось экспортировать DXF" });
  const mode = res.headers.get("X-GreenCity-Export") === "overlay" ? "overlay" : "rebuilt";
  return { blob: await res.blob(), mode };
}

// Объяснение каждой посадки со ссылкой на НПА и пункт (backend/greenplan/explanations.py).
export function downloadGreenPlanExplanations(
  scene: Scene,
  assignments: GreenPlanZoneAssignment[],
  rejections: GreenPlanRejections | undefined,
  format: "json" | "csv",
): Promise<Blob> {
  return requestBlob("/api/greenplan/explanations", {
    query: { format },
    json: { scene, assignments, rejections: rejections ?? { detailed: [], total: 0, by_rule: {} } },
    errorMessage: "Не удалось сформировать объяснения посадок",
  });
}

// Пояснительная записка GreenPlan (DOCX). scene -- текущая сцена с правками:
// нарушения и ведомость бэкенд пересчитывает по ней; report -- уже полученный
// текст, LLM заново не вызывается.
export function downloadGreenPlanDocument(
  scene: Scene,
  assignments: GreenPlanZoneAssignment[],
  report: string | null,
  title: string | null,
  options: GreenPlanOptions | null = null,
  notes: string[] = []
): Promise<Blob> {
  return requestBlob("/api/greenplan/document", {
    json: { scene, assignments, report, title, options, notes },
    errorMessage: "Не удалось сформировать пояснительную записку",
  });
}

// GreenPlan: расстановка -- детерминированно, без LLM, за секунды. Текст-
// обоснование (YandexGPT) -- отдельным запросом, чтобы не задерживать
// расстановку; недоступность модели -- не ошибка (report_error).
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
  // Точки, отклонённые по нормам, -- уходят обратно в файл объяснений.
  rejections?: GreenPlanRejections;
}

export interface GreenPlanRejections {
  detailed: Record<string, unknown>[];
  total: number;
  by_rule: Record<string, number>;
}

// Параметры GreenPlan (backend/greenplan/options.py) -- диалог перед запуском.
export interface GreenPlanOptions {
  style: "auto" | "regular" | "landscape";
  trees: boolean;
  bushes: boolean;
  lawn: boolean;
  // Убрать существующие деревья и кусты с нарушением норм до расстановки.
  remove_violating_plants: boolean;
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
  remove_violating_plants: false,
  preferred_trees: [],
  preferred_bushes: [],
  paths: false,
  lighting: false,
  benches: false,
};

export function generateGreenPlan(scene: Scene, options: GreenPlanOptions = DEFAULT_GREENPLAN_OPTIONS): Promise<GreenPlanGenerateResult> {
  return request<GreenPlanGenerateResult>("/api/greenplan/generate", { json: { scene, options }, errorMessage: "Не удалось построить GreenPlan" });
}

export interface GreenPlanReportResult {
  report: string | null;
  report_error: string | null;
}

// Отдельный запрос к LLM (YandexGPT) -- вызывающий код (EditorPage)
// не ждёт его перед тем, как показать уже готовый результат generateGreenPlan.
export function fetchGreenPlanReport(assignments: GreenPlanZoneAssignment[]): Promise<GreenPlanReportResult> {
  return request<GreenPlanReportResult>("/api/greenplan/report", { json: assignments, errorMessage: "Не удалось получить отчёт GreenPlan" });
}

export interface TextEditResult {
  scene: Scene;
  explanation: string;
  applied: string[];
  rejected: string[];
  warnings: string[];
  // Модель выбрала озеленение GreenPlan -- его запускает вызывающий код на scene.
  greenplan?: GreenPlanOptions | null;
  // id созданных объектов -- возвращаются в истории чата ("убери их").
  added_ids?: string[];
}

// Прошлая правка чата (backend/text_editor/operations.py::ChatTurn): без неё
// модель не понимает отсылок вроде "убери их".
export interface ChatTurn {
  instruction: string;
  explanation: string;
  applied: string[];
  added_ids: string[];
}

// Правка плана текстом через LLM (backend/text_editor/service.py). Модель отвечает
// несколько секунд -- вызывающему коду нужен индикатор ожидания.
export function editWithText(scene: Scene, instruction: string, history: ChatTurn[] = []): Promise<TextEditResult> {
  return request<TextEditResult>("/api/edit-with-text", { json: { scene, instruction, history }, errorMessage: "Не удалось применить правку" });
}

// Проекты -- только с входом в аккаунт. sessionAuth (auth.ts) сам обновляет
// истёкший access-токен. Лимит проектов проверяет бэкенд.

export interface ProjectSummary {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface Project extends ProjectSummary {
  scene: Scene;
}

export function listProjects(session: Session): Promise<ProjectSummary[]> {
  return request<ProjectSummary[]>("/api/projects", { authorize: sessionAuth(session), errorMessage: "Не удалось получить список проектов" });
}

// Без повторов: второй POST создал бы второй проект.
export function createProject(session: Session, name: string, scene: Scene): Promise<Project> {
  return request<Project>("/api/projects", { json: { name, scene }, authorize: sessionAuth(session) });
}

export function loadProject(session: Session, id: string): Promise<Project> {
  return request<Project>(`/api/projects/${id}`, { authorize: sessionAuth(session), errorMessage: "Не удалось открыть проект" });
}

// PUT идемпотентен -- при сбое сети повторяем сами.
export function saveProject(session: Session, id: string, scene: Scene): Promise<Project> {
  return request<Project>(`/api/projects/${id}`, {
    method: "PUT",
    json: { scene },
    retries: 2,
    authorize: sessionAuth(session),
    errorMessage: "Не удалось сохранить проект",
  });
}

export function renameProject(session: Session, id: string, name: string): Promise<Project> {
  return request<Project>(`/api/projects/${id}`, {
    method: "PUT",
    json: { name },
    retries: 2,
    authorize: sessionAuth(session),
    errorMessage: "Не удалось переименовать проект",
  });
}

export async function deleteProject(session: Session, id: string): Promise<void> {
  await send(`/api/projects/${id}`, { method: "DELETE", authorize: sessionAuth(session), errorMessage: "Не удалось удалить проект" });
}
