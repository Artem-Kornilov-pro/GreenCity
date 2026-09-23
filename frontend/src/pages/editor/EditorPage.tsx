import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { UploadCloud } from "lucide-react";
import { SceneView } from "../../scene/SceneView";
import type { TransformMode } from "../../scene/PlacedObjects";
import { fetchCatalog, fetchModelManifest, type CatalogItem } from "../../catalog";
import {
  uploadDxf,
  uploadDwgFolder,
  editWithText,
  exportDxf,
  createProject,
  loadProject,
  saveProject,
  generateGreenPlan,
  fetchGreenPlanReport,
  downloadGreenPlanDocument,
} from "../../api";
import { buildZoneIndex, checkViolationsAt, computeSceneBounds } from "../../geometry";
import { plantKindOfObjectType } from "../../setbackNorms";
import type { Point2, RestrictionZone, Scene, SceneObject } from "../../types";
import { useAuth } from "../../context/useAuth";
import { PageTransition } from "../../components/PageTransition";
import { AssistantPanel } from "./AssistantPanel";
import { EditorSidebar } from "./EditorSidebar";
import { EditorTopBar } from "./EditorTopBar";
import { GreenPlanPanel } from "./GreenPlanPanel";
import { SaveAsDialog } from "./SaveAsDialog";
import { StatusBanners } from "./StatusBanners";
import { makeId, SELECTION_ZONE_NAME, SELECTION_ZONE_TYPE, type ChatMessage, type GreenPlanState } from "./editorTypes";

// Страница редактора: состояние сцены и все обработчики -- здесь, отрисовка
// разнесена по частям в этой же папке (топбар, левая панель, панели
// Ассистента и GreenPlan, диалог сохранения).

export default function EditorPage() {
  const { projectId } = useParams<{ projectId?: string }>();
  const navigate = useNavigate();
  const { session } = useAuth();

  const [scene, setScene] = useState<Scene | null>(null);
  // Счётчик "загружена новая сцена" для FitCamera -- НЕ scene.boundary
  // напрямую: у реальных DWG-проектов без слоя границы (issue #50 follow-up)
  // boundary у ДВУХ РАЗНЫХ сцен подряд одинаково null, а null === null в JS
  // -- SceneView/FitCamera раньше принимал это за "та же сцена" и молча не
  // перецентровывал камеру на второй, третий и т.д. проект без границы,
  // оставляя её там, где она была для самого первого. Инкрементируется
  // только при загрузке ДЕЙСТВИТЕЛЬНО новой сцены (файл/папка/проект), не
  // при редактировании текущей (drag, правка текстом, GreenPlan) -- иначе
  // камера дёргалась бы на каждое такое действие.
  const [sceneLoadToken, setSceneLoadToken] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [transformMode, setTransformMode] = useState<TransformMode>("translate");
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [availableModels, setAvailableModels] = useState<Set<string>>(new Set());
  const [selectedItemId, setSelectedItemId] = useState<string>("");
  const [catalogFilter, setCatalogFilter] = useState("");
  const [hoveredZone, setHoveredZone] = useState<RestrictionZone | null>(null);
  const [loading, setLoading] = useState(false);
  const [exportingDxf, setExportingDxf] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Файлы .dwg, которые backend не смог сконвертировать при загрузке папки
  // (issue #50) -- не ошибка (сцена уже загружена и отображена), просто
  // предупреждение, отдельное от error, чтобы не выглядеть как сбой загрузки.
  // totalDwgFiles -- сколько .dwg вообще было отправлено (известно только на
  // клиенте, backend его не возвращает), для сообщения "N из M не удалось".
  const [dwgWarnings, setDwgWarnings] = useState<{ file: string; error: string }[] | null>(null);
  const [totalDwgFiles, setTotalDwgFiles] = useState(0);
  // Только для сообщения во время загрузки -- backend не отдаёт прогресс
  // по ходу конвертации (один HTTP-запрос на весь батч), а сама конвертация
  // не быстрая (issue #50 follow-up: реальный замер -- конвертация .dwg
  // сама по себе быстрая, ~1с/файл, а вот разбор итогового DXF занимает
  // 3-4.5с/файл на крупных реальных файлах -- параллелить его надёжно не
  // получилось, см. docstring backend/exchange/dwg_batch_converter.py). Оценка "~10с
  // на файл" не точный прогресс, а ожидание, чтобы не выглядело зависшим.
  const [dwgUploading, setDwgUploading] = useState(false);

  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [aiPanelOpen, setAiPanelOpen] = useState(false);

  const [greenPlanBusy, setGreenPlanBusy] = useState(false);
  const [greenPlanPanelOpen, setGreenPlanPanelOpen] = useState(false);
  const [greenPlanResult, setGreenPlanResult] = useState<GreenPlanState | null>(null);
  const [greenPlanReportLoading, setGreenPlanReportLoading] = useState(false);
  const [greenPlanDocBusy, setGreenPlanDocBusy] = useState(false);

  const [selectionMode, setSelectionMode] = useState(false);

  const [projectName, setProjectName] = useState<string | null>(null);
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");

  // Каталог -- источник правды по видам посадок (backend/core/plant_catalog.py).
  useEffect(() => {
    fetchCatalog()
      .then((items) => {
        setCatalog(items);
        setSelectedItemId((prev) => prev || items[0]?.id || "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    fetchModelManifest().then(setAvailableModels);
  }, []);

  // Если в адресе есть id проекта -- подгружаем его сцену при заходе.
  useEffect(() => {
    if (!projectId || !session) return;
    const run = () => {
      setLoading(true);
      loadProject(session, projectId)
        .then((project) => {
          setScene(project.scene);
          setSceneLoadToken((t) => t + 1);
          setProjectName(project.name);
          setSelectedId(null);
        })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    };
    run();
  }, [projectId, session]);

  const handleFile = useCallback(async (file: File) => {
    setLoading(true);
    setError(null);
    setDwgWarnings(null);
    try {
      const parsed = await uploadDxf(file);
      setScene(parsed);
      setSceneLoadToken((t) => t + 1);
      setSelectedId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Папка .dwg целиком (issue #50) -- webkitdirectory отдаёт ВСЕ файлы папки
  // (в реальных проектах Мосгеотреста рядом с .dwg лежат PDF/xlsx/фото),
  // поэтому фильтруем по расширению уже на клиенте, до отправки на бэкенд.
  const handleDwgFolder = useCallback(async (fileList: FileList) => {
    const dwgFiles = Array.from(fileList).filter((f) => f.name.toLowerCase().endsWith(".dwg"));
    if (dwgFiles.length === 0) {
      setError("В выбранной папке нет файлов .dwg");
      return;
    }
    setLoading(true);
    setDwgUploading(true);
    setError(null);
    setDwgWarnings(null);
    setTotalDwgFiles(dwgFiles.length);
    try {
      const parsed = await uploadDwgFolder(dwgFiles);
      setScene(parsed);
      setSceneLoadToken((t) => t + 1);
      setSelectedId(null);
      setDwgWarnings(parsed.dwgConversionWarnings ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setDwgUploading(false);
    }
  }, []);

  const handleMove = useCallback((id: string, x: number, z: number) => {
    setScene((prev) => (prev ? { ...prev, objects: prev.objects.map((o) => (o.id === id ? { ...o, position: { ...o.position, x, z } } : o)) } : prev));
  }, []);

  const handleSelect = useCallback((id: string | null) => {
    setSelectedId(id);
    setTransformMode("translate");
  }, []);

  const handleAddObject = useCallback((item: CatalogItem) => {
    setScene((prev) => {
      if (!prev) return prev;
      const bounds = computeSceneBounds(prev);
      const cx = (bounds.minX + bounds.maxX) / 2;
      const cz = (bounds.minZ + bounds.maxZ) / 2;
      const sameTypeCount = prev.objects.filter((o) => o.type === item.object_type).length;
      const offsetX = (sameTypeCount % 5) * 2.5;
      const offsetZ = Math.floor(sameTypeCount / 5) * 2.5;
      const id = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).slice(0, 8);
      const newObject: SceneObject = {
        id: `${item.object_type}_manual_${id}`,
        type: item.object_type,
        model: item.model,
        position: { x: cx + offsetX, y: 0, z: cz + offsetZ },
        rotation: 0,
        scale: 1,
        // species -- для правил отступа по породе (setbackNorms.ts: липе
        // 10 м от здания и т.п.); у МАФ и мощения нормы по породе нет.
        metadata: { catalogId: item.id, label: item.label, ...(item.setback_kind ? { species: item.label } : {}) },
      };
      setSelectedId(newObject.id);
      setTransformMode("translate");
      return { ...prev, objects: [...prev.objects, newObject] };
    });
  }, []);

  const handleDelete = useCallback((id: string) => {
    setScene((prev) => (prev ? { ...prev, objects: prev.objects.filter((o) => o.id !== id) } : prev));
    setSelectedId((prev) => (prev === id ? null : prev));
  }, []);

  const handleRotate = useCallback((id: string, rotationY: number) => {
    setScene((prev) => (prev ? { ...prev, objects: prev.objects.map((o) => (o.id === id ? { ...o, rotation: rotationY } : o)) } : prev));
  }, []);

  const handleAreaSelected = useCallback((polygon: Point2[]) => {
    setScene((prev) => {
      if (!prev) return prev;
      const zone: RestrictionZone = {
        id: `selection_${makeId()}`,
        type: SELECTION_ZONE_TYPE,
        name: SELECTION_ZONE_NAME,
        polygon,
        severity: "allowed",
        minDistance: 0,
        message: "Зона, выделенная вручную для правки текстом",
      };
      // Одно активное выделение за раз -- новое заменяет предыдущее, а не
      // накапливается рядом с ним.
      const withoutOldSelection = prev.restrictions.filter((z) => z.type !== SELECTION_ZONE_TYPE);
      return { ...prev, restrictions: [...withoutOldSelection, zone] };
    });
    setSelectionMode(false);
  }, []);

  const handleClearSelection = useCallback(() => {
    setScene((prev) => (prev ? { ...prev, restrictions: prev.restrictions.filter((z) => z.type !== SELECTION_ZONE_TYPE) } : prev));
  }, []);

  const handleTextEdit = useCallback(async () => {
    if (!scene || !instruction.trim()) return;
    const text = instruction.trim();
    setMessages((prev) => [...prev, { id: makeId(), role: "user", text }]);
    setInstruction("");
    setEditing(true);
    try {
      const result = await editWithText(scene, text);
      setScene(result.scene);
      setSelectedId((prev) => (prev && result.scene.objects.some((o) => o.id === prev) ? prev : null));
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "assistant",
          text: result.explanation || `Применено изменений: ${result.applied.length}`,
          applied: result.applied,
          rejected: result.rejected,
          warnings: result.warnings,
        },
      ]);
    } catch (e) {
      setMessages((prev) => [...prev, { id: makeId(), role: "error", text: e instanceof Error ? e.message : String(e) }]);
    } finally {
      setEditing(false);
    }
  }, [scene, instruction]);

  const handleGreenPlan = useCallback(async () => {
    if (!scene) return;
    setGreenPlanBusy(true);
    setError(null);
    try {
      const result = await generateGreenPlan(scene);
      setScene(result.scene);
      // Расстановка/нарушения/ведомость уже готовы -- показываем сразу, не
      // дожидаясь текста-объяснения (тот -- ~30 секунд, локальная LLM).
      setGreenPlanResult({ ...result, report: null, report_error: null });
      setAiPanelOpen(false);
      setGreenPlanPanelOpen(true);
      setGreenPlanBusy(false);

      // Отдельно, в фоне: не await'ится этим же try -- ошибка здесь не
      // должна откатывать уже показанный результат расстановки.
      setGreenPlanReportLoading(true);
      try {
        const { report, report_error } = await fetchGreenPlanReport(result.assignments);
        setGreenPlanResult((prev) => (prev ? { ...prev, report, report_error } : prev));
      } catch (e) {
        setGreenPlanResult((prev) =>
          (prev ? { ...prev, report: null, report_error: e instanceof Error ? e.message : String(e) } : prev),
        );
      } finally {
        setGreenPlanReportLoading(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setGreenPlanBusy(false);
    }
  }, [scene]);

  const handleExportJson = () => {
    if (!scene) return;
    const blob = new Blob([JSON.stringify(scene.objects, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "objects.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportDxf = useCallback(async () => {
    if (!scene) return;
    setExportingDxf(true);
    setError(null);
    try {
      const blob = await exportDxf(scene);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "greencity_plan.dxf";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportingDxf(false);
    }
  }, [scene]);

  const handleDownloadGreenPlanDocument = useCallback(async () => {
    if (!scene || !greenPlanResult) return;
    setGreenPlanDocBusy(true);
    setError(null);
    try {
      const blob = await downloadGreenPlanDocument(scene, greenPlanResult.assignments, greenPlanResult.report, projectName);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Пояснительная записка — ${projectName ?? "участок"}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGreenPlanDocBusy(false);
    }
  }, [scene, greenPlanResult, projectName]);

  const handleSaveCurrent = useCallback(async () => {
    if (!session || !scene || !projectId) return;
    setProjectBusy(true);
    setProjectError(null);
    try {
      await saveProject(session, projectId, scene);
    } catch (e) {
      setProjectError(e instanceof Error ? e.message : String(e));
    } finally {
      setProjectBusy(false);
    }
  }, [session, scene, projectId]);

  const handleSaveAs = useCallback(async () => {
    if (!session || !scene || !saveAsName.trim()) return;
    setProjectBusy(true);
    setProjectError(null);
    try {
      const created = await createProject(session, saveAsName.trim(), scene);
      setSaveAsOpen(false);
      setSaveAsName("");
      navigate(`/editor/${created.id}`, { replace: true });
    } catch (e) {
      setProjectError(e instanceof Error ? e.message : String(e));
    } finally {
      setProjectBusy(false);
    }
  }, [session, scene, saveAsName, navigate]);

  useEffect(() => {
    if (!selectedId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        handleDelete(selectedId);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId, handleDelete]);

  const catalogById = useMemo(() => new Map(catalog.map((i) => [i.id, i])), [catalog]);
  const filteredCatalog = useMemo(() => {
    const q = catalogFilter.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter((i) => i.label.toLowerCase().includes(q));
  }, [catalog, catalogFilter]);
  const effectiveItemId = filteredCatalog.some((i) => i.id === selectedItemId) ? selectedItemId : (filteredCatalog[0]?.id ?? "");
  const selectedItem = catalogById.get(effectiveItemId);

  const selectedArea = scene?.restrictions.find((z) => z.type === SELECTION_ZONE_TYPE) ?? null;

  // scene меняется по ссылке на каждое перемещение/добавление объекта, но
  // restrictions -- только когда меняется сама разметка участка (загрузка
  // DXF, GreenPlan) -- индекс не нужно перестраивать на каждый drag.
  const restrictionZoneIndex = useMemo(() => buildZoneIndex(scene?.restrictions ?? []), [scene?.restrictions]);

  // Раньше это пересчитывалось на КАЖДЫЙ ре-рендер страницы линейным
  // checkViolations по всем зонам сцены (тысячи на реальных участках) -- в
  // частности, на каждое наведение мыши на зону ограничения (см. onHover в
  // RestrictionZones.tsx), не имеющее отношения к выделенному объекту.
  // useMemo + индексированный checkViolationsAt пересчитывают только когда
  // реально меняется само выделение или сцена.
  const selectedObj = useMemo(
    () => scene?.objects.find((o) => o.id === selectedId) ?? null,
    [scene?.objects, selectedId]
  );
  const selectedViolations = useMemo(
    () =>
      selectedObj
        ? checkViolationsAt(
            selectedObj.position.x,
            selectedObj.position.z,
            restrictionZoneIndex,
            plantKindOfObjectType(selectedObj.type),
            typeof selectedObj.metadata.species === "string" ? selectedObj.metadata.species : undefined
          )
        : [],
    [selectedObj, restrictionZoneIndex]
  );

  return (
    <PageTransition>
      <div className="relative flex h-screen flex-col overflow-hidden bg-ink-50">
        <EditorTopBar
          title={projectName ?? (projectId ? "Загрузка…" : "Новый проект")}
          loading={loading}
          onDxfFile={handleFile}
          onDwgFolder={handleDwgFolder}
          sceneLoaded={scene !== null}
          selectionMode={selectionMode}
          onToggleSelection={() => setSelectionMode((v) => !v)}
          assistantOpen={aiPanelOpen}
          onToggleAssistant={() => {
            setGreenPlanPanelOpen(false);
            setAiPanelOpen((v) => !v);
          }}
          greenPlanBusy={greenPlanBusy}
          onGreenPlan={handleGreenPlan}
          exportingDxf={exportingDxf}
          onExportDxf={handleExportDxf}
          onExportJson={handleExportJson}
          isSavedProject={Boolean(projectId)}
          projectBusy={projectBusy}
          onSave={() => (projectId ? handleSaveCurrent() : setSaveAsOpen(true))}
        />

        <StatusBanners
          error={error ?? projectError}
          dwgUploading={dwgUploading}
          totalDwgFiles={totalDwgFiles}
          dwgWarnings={dwgWarnings}
          onDismissDwgWarnings={() => setDwgWarnings(null)}
        />

        <main className="relative flex-1 overflow-hidden">
          {scene ? (
            <SceneView
              scene={scene}
              sceneLoadToken={sceneLoadToken}
              catalogById={catalogById}
              availableModels={availableModels}
              selectedId={selectedId}
              transformMode={transformMode}
              selectionMode={selectionMode}
              onSelect={handleSelect}
              onMove={handleMove}
              onRotate={handleRotate}
              onHoverZone={setHoveredZone}
              onAreaSelected={handleAreaSelected}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-ink-400">
              <UploadCloud className="h-10 w-10" />
              <p>Загрузите DXF-файл, чтобы увидеть сцену</p>
            </div>
          )}

          <EditorSidebar
            sceneLoaded={scene !== null}
            catalog={catalog}
            filteredCatalog={filteredCatalog}
            availableModelsCount={availableModels.size}
            catalogFilter={catalogFilter}
            onCatalogFilterChange={setCatalogFilter}
            selectedItemId={effectiveItemId}
            onSelectItem={setSelectedItemId}
            selectedItem={selectedItem}
            onAddObject={handleAddObject}
            selectedArea={selectedArea}
            onClearSelection={handleClearSelection}
            hoveredZone={hoveredZone}
            selectedObject={selectedObj}
            selectedViolations={selectedViolations}
            transformMode={transformMode}
            onTransformModeChange={setTransformMode}
            onDelete={handleDelete}
          />

          <AssistantPanel
            open={aiPanelOpen}
            onToggle={() => setAiPanelOpen((v) => !v)}
            onClose={() => setAiPanelOpen(false)}
            messages={messages}
            editing={editing}
            instruction={instruction}
            onInstructionChange={setInstruction}
            onSubmit={handleTextEdit}
            sceneLoaded={scene !== null}
          />

          <GreenPlanPanel
            open={greenPlanPanelOpen}
            onToggle={() => setGreenPlanPanelOpen((v) => !v)}
            onClose={() => setGreenPlanPanelOpen(false)}
            result={greenPlanResult}
            reportLoading={greenPlanReportLoading}
            documentBusy={greenPlanDocBusy}
            onDownloadDocument={handleDownloadGreenPlanDocument}
          />
        </main>
      </div>

      <SaveAsDialog
        open={saveAsOpen}
        onOpenChange={setSaveAsOpen}
        name={saveAsName}
        onNameChange={setSaveAsName}
        busy={projectBusy}
        onSave={handleSaveAs}
      />
    </PageTransition>
  );
}
