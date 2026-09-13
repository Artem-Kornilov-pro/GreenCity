import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import {
  Leaf,
  UploadCloud,
  Sparkles,
  Download,
  Save,
  FolderKanban,
  LogOut,
  User,
  Trash2,
  Loader2,
  Move,
  RotateCw,
  Plus,
  X,
  Send,
  ChevronLeft,
  LassoSelect,
} from "lucide-react";
import { SceneView } from "../scene/SceneView";
import type { TransformMode } from "../scene/PlacedObjects";
import { CATEGORY_LABELS, fetchCatalog, fetchModelManifest, type CatalogCategory, type CatalogItem } from "../catalog";
import {
  uploadDxf,
  editWithText,
  exportDxf,
  createProject,
  loadProject,
  saveProject,
} from "../api";
import { checkViolations, computeSceneBounds } from "../geometry";
import { plantKindOfObjectType } from "../setbackNorms";
import type { Point2, RestrictionZone, Scene, SceneObject } from "../types";
import { useAuth } from "../context/useAuth";
import { PageTransition } from "../components/PageTransition";
import { Button } from "../components/ui/button";
import { Input, Textarea } from "../components/ui/input";
import { Card } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";

function makeId(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

// Зона выделения мышкой (issue "Выделение участка карты мышкой") -- тип
// строго "selection", это то, по чему backend/llm_editor.py::_build_context
// узнаёт её в scene.restrictions и отдаёт модели как selected_areas, отдельно
// от обычных зон плана. severity "allowed" -- сама по себе ничего не
// запрещает (Placer.region() её игнорирует), это просто именованный маркер.
const SELECTION_ZONE_TYPE = "selection";
const SELECTION_ZONE_NAME = "Выделение";

interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  applied?: string[];
  rejected?: string[];
  warnings?: string[];
}

export default function EditorPage() {
  const { projectId } = useParams<{ projectId?: string }>();
  const navigate = useNavigate();
  const { session, logout } = useAuth();

  const [scene, setScene] = useState<Scene | null>(null);
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

  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [aiPanelOpen, setAiPanelOpen] = useState(false);

  const [selectionMode, setSelectionMode] = useState(false);

  const [projectName, setProjectName] = useState<string | null>(null);
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");

  // Каталог -- источник правды по видам посадок (backend/plant_catalog.py).
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
    try {
      const parsed = await uploadDxf(file);
      setScene(parsed);
      setSelectedId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
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
        metadata: { catalogId: item.id, label: item.label },
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

  const selectedObj = scene?.objects.find((o) => o.id === selectedId) ?? null;
  const selectedViolations = selectedObj
    ? checkViolations(selectedObj.position.x, selectedObj.position.z, scene?.restrictions ?? [], plantKindOfObjectType(selectedObj.type))
    : [];

  return (
    <PageTransition>
      <div className="relative flex h-screen flex-col overflow-hidden bg-ink-50">
        {/* Топбар -- плавает поверх сцены (не занимает место в потоке), поэтому
            сквозь него видна и блюрится сама 3D-сцена, а не плоский фон
            страницы -- без этого эффект "жидкого стекла" на однотонном фоне
            почти не заметен. */}
        <header className="absolute inset-x-0 top-0 z-40 flex h-14 items-center gap-3 border-b border-white/20 bg-white/10 px-4 shadow-sm backdrop-blur-2xl backdrop-saturate-150">
          <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold text-ink-900">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white">
              <Leaf className="h-4 w-4" />
            </span>
            <span className="hidden sm:inline">GreenCity</span>
          </Link>

          <div className="mx-2 h-5 w-px bg-ink-200" />

          <span className="truncate text-sm font-medium text-ink-700">{projectName ?? (projectId ? "Загрузка…" : "Новый проект")}</span>

          <div className="ml-auto flex items-center gap-1.5">
            <label>
              <Button asChild variant="outline" size="sm">
                <span className="cursor-pointer">
                  {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
                  Загрузить DXF
                </span>
              </Button>
              <input type="file" accept=".dxf" hidden onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])} />
            </label>

            {scene && (
              <>
                <Button
                  size="sm"
                  variant={selectionMode ? "primary" : "outline"}
                  onClick={() => setSelectionMode((v) => !v)}
                  title="Обвести участок на плане мышкой -- потом можно сослаться на него в правке текстом («посади здесь кусты»)"
                >
                  <LassoSelect className="h-3.5 w-3.5" />
                  {selectionMode ? "Обводите на плане…" : "Выделить зону"}
                </Button>
                <Button
                  size="sm"
                  variant={aiPanelOpen ? "outline" : "primary"}
                  onClick={() => setAiPanelOpen((v) => !v)}
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  Ассистент
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" disabled={exportingDxf}>
                      {exportingDxf ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                      Экспорт
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={handleExportDxf}>Экспорт в DXF</DropdownMenuItem>
                    <DropdownMenuItem onClick={handleExportJson}>Экспорт в JSON</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>

                {session && (
                  <Button size="sm" variant="outline" onClick={() => (projectId ? handleSaveCurrent() : setSaveAsOpen(true))} disabled={projectBusy}>
                    {projectBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                    {projectId ? "Сохранить" : "Сохранить как…"}
                  </Button>
                )}
              </>
            )}

            <div className="mx-1 h-5 w-px bg-ink-200" />

            {session ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm">
                    <User className="h-3.5 w-3.5" />
                    {session.username}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuLabel>{session.username}</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => navigate("/projects")}>
                    <FolderKanban className="h-4 w-4" />
                    Мои проекты
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      logout();
                      navigate("/");
                    }}
                  >
                    <LogOut className="h-4 w-4" />
                    Выйти
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <>
                <Button asChild variant="ghost" size="sm">
                  <Link to="/login">Войти</Link>
                </Button>
                <Button asChild size="sm">
                  <Link to="/register">Регистрация</Link>
                </Button>
              </>
            )}
          </div>
        </header>

        {(error || projectError) && (
          <div className="absolute inset-x-0 top-14 z-30 border-b border-danger-500/20 bg-danger-500/90 px-4 py-2 text-sm text-white backdrop-blur-sm">
            {error ?? projectError}
          </div>
        )}

        {/* Контент -- обе боковые панели плавают поверх сцены (как и топбар),
            иначе сквозь них нечего блюрить, кроме однотонного фона страницы,
            и эффект стекла не виден. */}
        <main className="relative flex-1 overflow-hidden">
          {scene ? (
            <SceneView
              scene={scene}
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

          <aside className="scrollbar-thin absolute left-0 top-0 z-20 flex h-full w-80 flex-col gap-3 overflow-y-auto border-r border-white/20 bg-white/10 px-3 pb-3 pt-16 shadow-sm backdrop-blur-2xl backdrop-saturate-150">
            {scene && (
              <Card className="border-white/30 bg-white/25 p-4 backdrop-blur-md">
                <h3 className="text-sm font-semibold text-ink-900">Добавить объект</h3>
                <Input
                  className="mt-3"
                  placeholder="Поиск: раскидистое, низкое, сосна…"
                  value={catalogFilter}
                  onChange={(e) => setCatalogFilter(e.target.value)}
                />
                <div className="mt-2 flex gap-2">
                  <select
                    className="h-10 w-full rounded-xl border border-ink-200 bg-white px-3 text-sm text-ink-900 focus:outline-none focus:ring-2 focus:ring-brand-400"
                    value={effectiveItemId}
                    onChange={(e) => setSelectedItemId(e.target.value)}
                  >
                    {(Object.keys(CATEGORY_LABELS) as CatalogCategory[]).map((category) => {
                      const items = filteredCatalog.filter((i) => i.category === category);
                      if (items.length === 0) return null;
                      return (
                        <optgroup key={category} label={CATEGORY_LABELS[category]}>
                          {items.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.label}
                            </option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                  <Button size="icon" disabled={!selectedItem} onClick={() => selectedItem && handleAddObject(selectedItem)}>
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                <p className="mt-2 text-xs text-ink-400">
                  {catalogFilter ? `${filteredCatalog.length} из ${catalog.length}` : `${catalog.length} видов`} в каталоге
                  {availableModels.size > 0 ? `, 3D-моделей: ${availableModels.size}` : ", модели не подключены — рисуются заглушки"}
                </p>
              </Card>
            )}

            {selectedArea && (
              <Card className="border-white/30 bg-white/25 p-4 backdrop-blur-md">
                <div className="flex items-center justify-between">
                  <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink-900">
                    <LassoSelect className="h-3.5 w-3.5 text-success-500" />
                    {selectedArea.name}
                  </h3>
                  <Button size="icon" variant="ghost" title="Снять выделение" onClick={handleClearSelection}>
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <p className="mt-1 text-xs text-ink-400">
                  Можно сослаться на неё в правке текстом: «посади здесь кусты», «убери отсюда лавки».
                </p>
              </Card>
            )}

            {hoveredZone && (
              <Card className="border-white/30 bg-white/25 p-4 backdrop-blur-md">
                <h3 className="text-sm font-semibold text-ink-900">{hoveredZone.name}</h3>
                <p className="mt-1 text-sm text-ink-500">{hoveredZone.message}</p>
                <p className="mt-2 text-xs text-ink-400">
                  severity: {hoveredZone.severity}, minDistance: {hoveredZone.minDistance} м
                </p>
              </Card>
            )}

            {selectedObj && (
              <Card className="border-white/30 bg-white/25 p-4 backdrop-blur-md">
                <h3 className="text-sm font-semibold text-ink-900">
                  {selectedObj.type} <span className="font-normal text-ink-400">({selectedObj.id})</span>
                </h3>
                <p className="mt-1 text-xs text-ink-400">
                  x={selectedObj.position.x.toFixed(2)} z={selectedObj.position.z.toFixed(2)}, поворот=
                  {((selectedObj.rotation * 180) / Math.PI).toFixed(0)}°
                </p>
                <div className="mt-3 flex gap-1.5">
                  <Button size="sm" variant={transformMode === "translate" ? "primary" : "outline"} onClick={() => setTransformMode("translate")}>
                    <Move className="h-3.5 w-3.5" />
                    Двигать
                  </Button>
                  <Button size="sm" variant={transformMode === "rotate" ? "primary" : "outline"} onClick={() => setTransformMode("rotate")}>
                    <RotateCw className="h-3.5 w-3.5" />
                    Вращать
                  </Button>
                </div>
                {selectedViolations.length > 0 ? (
                  <div className="mt-3 flex flex-col gap-1.5">
                    {selectedViolations.map((v) => (
                      <Badge key={v.zone.id} variant="warning" className="justify-start">
                        ⚠ {v.zone.message} (мин. {v.minDistance} м)
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <Badge variant="success" className="mt-3">
                    Нарушений отступов нет
                  </Badge>
                )}
                <Button variant="danger" size="sm" className="mt-3 w-full" onClick={() => handleDelete(selectedObj.id)}>
                  <Trash2 className="h-3.5 w-3.5" />
                  Удалить (Delete)
                </Button>
              </Card>
            )}

            {scene && (
              <Card className="border-white/30 bg-white/25 p-4 text-xs text-ink-500 backdrop-blur-md">
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-danger-500" /> запрещено
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-warning-500" /> предупреждение
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-success-500" /> разрешено
                  </div>
                </div>
                <p className="mt-3 leading-relaxed text-ink-400">
                  Клик по объекту — выбрать. «Двигать» — тащить по земле, «Вращать» — вокруг своей оси. Delete/Backspace — удалить
                  выбранный объект.
                </p>
              </Card>
            )}
          </aside>

          {/* Ассистент -- выезжающая справа панель, а не модальное окно */}
            <motion.aside
              initial={false}
              animate={{ x: aiPanelOpen ? 0 : "100%" }}
              transition={{ type: "spring", stiffness: 320, damping: 34 }}
              className="absolute right-0 top-14 z-30 flex h-[calc(100%-3.5rem)] w-full max-w-md flex-col border-l border-white/30 bg-white/20 shadow-soft-lg backdrop-blur-xl backdrop-saturate-150"
            >
              {/* Ручка-выдвигалка -- прикреплена к левому краю панели, поэтому
                  когда панель уезжает вправо (закрыта), ручка выезжает вместе с
                  ней и в итоге остаётся видна у самого края экрана. */}
              <button
                type="button"
                onClick={() => setAiPanelOpen((v) => !v)}
                aria-label={aiPanelOpen ? "Свернуть ассистента" : "Развернуть ассистента"}
                className="absolute -left-8 top-1/2 flex h-16 w-8 -translate-y-1/2 items-center justify-center rounded-l-xl border border-r-0 border-white/30 bg-white/20 text-brand-600 shadow-soft backdrop-blur-xl backdrop-saturate-150 transition-colors hover:bg-white/40"
              >
                <motion.span animate={{ rotate: aiPanelOpen ? 180 : 0 }} transition={{ duration: 0.25 }}>
                  <ChevronLeft className="h-4 w-4" />
                </motion.span>
              </button>

              <div className="flex items-center justify-between border-b border-ink-200/70 px-4 py-3">
                <div className="flex items-center gap-2 font-semibold text-ink-900">
                  <Sparkles className="h-4.5 w-4.5 text-brand-600" />
                  Ассистент
                </div>
                <Button variant="ghost" size="icon" onClick={() => setAiPanelOpen(false)}>
                  <X className="h-4 w-4" />
                </Button>
              </div>

              <div className="scrollbar-thin flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
                {messages.length === 0 && (
                  <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center text-sm text-ink-400">
                    <Sparkles className="h-8 w-8 text-brand-300" />
                    <p>Опишите на русском, что изменить в плане — например «посади 5 деревьев вдоль южного дома».</p>
                  </div>
                )}
                {messages.map((m) => (
                  <div
                    key={m.id}
                    className={
                      m.role === "user"
                        ? "max-w-[85%] self-end rounded-2xl rounded-br-sm bg-brand-600 px-3.5 py-2 text-sm text-white"
                        : m.role === "error"
                          ? "max-w-[85%] self-start rounded-2xl rounded-bl-sm bg-danger-500/10 px-3.5 py-2 text-sm text-danger-500"
                          : "max-w-[85%] self-start rounded-2xl rounded-bl-sm bg-ink-100 px-3.5 py-2 text-sm text-ink-800"
                    }
                  >
                    <p>{m.text}</p>
                    {((m.rejected && m.rejected.length > 0) || (m.warnings && m.warnings.length > 0)) && (
                      <div className="mt-1.5 flex flex-col gap-1 border-t border-ink-900/10 pt-1.5 text-xs opacity-80">
                        {m.rejected?.map((r, i) => (
                          <p key={`r-${i}`}>✕ {r}</p>
                        ))}
                        {m.warnings?.map((w, i) => (
                          <p key={`w-${i}`}>⚠ {w}</p>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {editing && (
                  <div className="max-w-[85%] self-start rounded-2xl rounded-bl-sm bg-ink-100 px-3.5 py-2 text-sm text-ink-500">
                    Думаю…
                  </div>
                )}
              </div>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleTextEdit();
                }}
                className="flex items-end gap-2 border-t border-ink-200/70 p-3"
              >
                <Textarea
                  rows={2}
                  placeholder="Например: убери лавки у парковки"
                  value={instruction}
                  disabled={editing || !scene}
                  onChange={(e) => setInstruction(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleTextEdit();
                    }
                  }}
                  className="flex-1 resize-none"
                />
                <Button type="submit" size="icon" disabled={editing || !scene || !instruction.trim()}>
                  {editing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </Button>
              </form>
            </motion.aside>
        </main>
      </div>

      {/* Сохранить как новый проект */}
      <Dialog open={saveAsOpen} onOpenChange={setSaveAsOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Сохранить как новый проект</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSaveAs();
            }}
            className="flex flex-col gap-3"
          >
            <Input placeholder="Название проекта" value={saveAsName} onChange={(e) => setSaveAsName(e.target.value)} autoFocus />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setSaveAsOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" disabled={!saveAsName.trim() || projectBusy}>
                {projectBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Сохранить"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </PageTransition>
  );
}
