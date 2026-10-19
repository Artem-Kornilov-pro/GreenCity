import { sessionAuth, type Session } from "./auth";
import { request, requestBlob, send } from "./lib/http";
import type { LayerKind, Scene } from "./types";

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

// Классы слоёв, которые можно назначить вручную в окне «Слои чертежа».
export function getLayerKinds(): Promise<LayerKind[]> {
  return request<LayerKind[]>("/api/layer-kinds", { errorMessage: "Не удалось получить список типов слоёв" });
}

// Загруженный чертёж (DXF или пачка DWG) разбирается заново на сервере с
// назначениями слоёв: файлы второй раз не отправляются.
// boundary -- граница участка, выбранная из meta.boundaryCandidates (или уже
// выбранная раньше: meta.boundaryOverride), в координатах чертежа.
export function reparseScene(sourceId: string, layerMap: Record<string, string>, boundary?: number[][] | null): Promise<Scene> {
  return request<Scene>("/api/reparse", {
    json: { sourceId, layerMap, boundary: boundary ?? null },
    errorMessage: "Не удалось разобрать чертёж заново",
  });
}

// Итоговый план -> .dxf. mode: "overlay" -- слои результата дописаны в
// исходный чертёж (исходные слои и координаты не тронуты), "rebuilt" --
// исходника на сервере нет, DXF собран из сцены.
export async function exportDxf(scene: Scene): Promise<{ blob: Blob; mode: "overlay" | "rebuilt" }> {
  const res = await send("/api/export-dxf", { json: scene, errorMessage: "Не удалось экспортировать DXF" });
  const mode = res.headers.get("X-GreenCity-Export") === "overlay" ? "overlay" : "rebuilt";
  return { blob: await res.blob(), mode };
}

// Независимая проверка выгрузки (backend/exchange/export_check.py): выходной
// DXF читается заново -- исходник цел, посадки на местах, нормы соблюдены.
export interface ExportVerification {
  ok: boolean;
  mode: "overlay" | "rebuilt";
  source_sha256: string | null;
  output_sha256: string;
  versions: Record<string, string>;
  source_entities_checked: number;
  source_entities_missing: number;
  source_entities_changed: number;
  source_check: string;
  plants_checked: number;
  plants_missing: string[];
  plants_moved: string[];
  violations: { object_id: string; zone_type: string; distance_m: number; required_m: number }[];
}

export function verifyExport(scene: Scene): Promise<ExportVerification> {
  return request<ExportVerification>("/api/export-dxf/verify", { json: scene, errorMessage: "Не удалось проверить выгрузку" });
}

// Объяснение каждой посадки со ссылкой на НПА и пункт (backend/greenplan/explanations.py).
export function downloadGreenPlanExplanations(
  scene: Scene,
  assignments: GreenPlanZoneAssignment[],
  rejections: GreenPlanRejections | undefined,
  format: "json" | "csv" | "xlsx",
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
  // Деревья ближе нормы к подземным сетям с прикорневым барьером («условно»).
  root_barrier: boolean;
  // Класс напряжения ЛЭП без подписи в имени слоя, кВ; null -- 10 кВ.
  overhead_voltage_kv: number | null;
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
  root_barrier: false,
  overhead_voltage_kv: null,
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

// Иллюстрации участка после озеленения (Alice AI ART) -- по решениям GreenPlan.
export interface GreenPlanVisualizations {
  note: string;
  visualizations: { view: string; prompt: string; image: string }[];
}

export function generateGreenPlanVisualizations(
  scene: Scene,
  assignments: GreenPlanZoneAssignment[],
  seed = 1,
): Promise<GreenPlanVisualizations> {
  return request<GreenPlanVisualizations>("/api/greenplan/visualizations", {
    json: { scene, assignments, seed },
    errorMessage: "Не удалось получить визуализации",
  });
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
  files_count?: number;
  shared?: boolean;
}

// Файл проекта (backend/accounts/schemas.py ProjectFile): документы и
// картинки по результату GreenPlan.
export interface ProjectFile {
  id: string;
  name: string;
  kind: "docx" | "explanations_json" | "explanations_csv" | "explanations_xlsx" | "dxf" | "export_check" | "visualization" | string;
  media_type: string;
  size: number;
  created_at: string;
  auto: boolean;
}

export interface Project extends ProjectSummary {
  scene: Scene;
  files?: ProjectFile[];
  share_token?: string | null;
}

// Проект по ссылке -- только просмотр.
export interface SharedProject {
  name: string;
  scene: Scene;
  updated_at: string;
  files: ProjectFile[];
}

export interface GreenPlanFilesResult {
  files: ProjectFile[];
  notes: string[];
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

// Сохранить результат GreenPlan в проект и подготовить комплект файлов
// (записка, объяснения, DXF с проверкой, визуализации). На крупном объекте --
// минуты. Без повторов: каждый запуск -- новый комплект и платные картинки.
export function prepareGreenPlanFiles(
  session: Session,
  id: string,
  body: {
    scene: Scene;
    assignments: GreenPlanZoneAssignment[];
    rejections: unknown;
    report: string | null;
    options: GreenPlanOptions;
    notes: string[];
    visualizations: boolean;
  },
): Promise<GreenPlanFilesResult> {
  return request<GreenPlanFilesResult>(`/api/projects/${id}/greenplan-files`, {
    json: body,
    authorize: sessionAuth(session),
    errorMessage: "Не удалось подготовить файлы проекта",
  });
}

export function downloadProjectFile(session: Session, projectId: string, fileId: string): Promise<Blob> {
  return requestBlob(`/api/projects/${projectId}/files/${fileId}`, { authorize: sessionAuth(session), errorMessage: "Не удалось скачать файл" });
}

export async function deleteProjectFile(session: Session, projectId: string, fileId: string): Promise<void> {
  await send(`/api/projects/${projectId}/files/${fileId}`, { method: "DELETE", authorize: sessionAuth(session), errorMessage: "Не удалось удалить файл" });
}

export async function setProjectSharing(session: Session, id: string, enabled: boolean): Promise<string | null> {
  const r = await request<{ share_token: string | null }>(`/api/projects/${id}/share`, {
    json: { enabled },
    authorize: sessionAuth(session),
    errorMessage: "Не удалось изменить доступ по ссылке",
  });
  return r.share_token;
}

export function loadSharedProject(token: string): Promise<SharedProject> {
  return request<SharedProject>(`/api/shared/${token}`, { errorMessage: "Не удалось открыть проект по ссылке" });
}

export function downloadSharedFile(token: string, fileId: string): Promise<Blob> {
  return requestBlob(`/api/shared/${token}/files/${fileId}`, { errorMessage: "Не удалось скачать файл" });
}

// Адрес страницы проекта по ссылке.
export function shareUrl(token: string): string {
  return `${window.location.origin}/share/${token}`;
}
