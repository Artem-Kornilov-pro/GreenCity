import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
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
} from "lucide-react";
import { SceneView } from "../scene/SceneView";
import type { TransformMode } from "../scene/PlacedObjects";
import { CATEGORY_LABELS, fetchCatalog, fetchModelManifest, type CatalogCategory, type CatalogItem } from "../catalog";
import {
  uploadDxf,
  generateGreenery,
  editWithText,
  exportDxf,
  createProject,
  loadProject,
  saveProject,
  type TextEditResult,
} from "../api";
import { checkViolations, computeSceneBounds } from "../geometry";
import { plantKindOfObjectType } from "../setbackNorms";
import type { RestrictionZone, Scene, SceneObject } from "../types";
import { useAuth } from "../context/useAuth";
import { PageTransition } from "../components/PageTransition";
import { Button } from "../components/ui/button";
import { Input, Textarea } from "../components/ui/input";
import { Card } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";

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
  const [generating, setGenerating] = useState(false);
  const [exportingDxf, setExportingDxf] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [editResult, setEditResult] = useState<TextEditResult | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [aiDialogOpen, setAiDialogOpen] = useState(false);

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

  const handleGenerateGreenery = useCallback(async () => {
    if (!scene) return;
    setGenerating(true);
    setError(null);
    try {
      const updated = await generateGreenery(scene);
      if (updated) setScene(updated);
      else setError("Автогенерация растительности пока не реализована на бэкенде.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }, [scene]);

  const handleTextEdit = useCallback(async () => {
    if (!scene || !instruction.trim()) return;
    setEditing(true);
    setEditError(null);
    setEditResult(null);
    try {
      const result = await editWithText(scene, instruction.trim());
      setScene(result.scene);
      setEditResult(result);
      setInstruction("");
      setSelectedId((prev) => (prev && result.scene.objects.some((o) => o.id === prev) ? prev : null));
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
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

  const selectedObj = scene?.objects.find((o) => o.id === selectedId) ?? null;
  const selectedViolations = selectedObj
    ? checkViolations(selectedObj.position.x, selectedObj.position.z, scene?.restrictions ?? [], plantKindOfObjectType(selectedObj.type))
    : [];

  return (
    <PageTransition>
      <div className="flex h-screen flex-col overflow-hidden bg-ink-50">
        {/* Топбар */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-ink-200/70 bg-white px-4">
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
                <Button variant="outline" size="sm" onClick={handleGenerateGreenery} disabled={generating}>
                  {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  Сгенерировать
                </Button>
                <Button size="sm" onClick={() => setAiDialogOpen(true)}>
                  <Sparkles className="h-3.5 w-3.5" />
                  Правка ИИ
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
          <div className="border-b border-danger-500/20 bg-danger-500/10 px-4 py-2 text-sm text-danger-500">{error ?? projectError}</div>
        )}

        {/* Контент */}
        <div className="flex flex-1 overflow-hidden">
          <main className="relative flex-1">
            {scene ? (
              <SceneView
                scene={scene}
                catalogById={catalogById}
                availableModels={availableModels}
                selectedId={selectedId}
                transformMode={transformMode}
                onSelect={handleSelect}
                onMove={handleMove}
                onRotate={handleRotate}
                onHoverZone={setHoveredZone}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-ink-400">
                <UploadCloud className="h-10 w-10" />
                <p>Загрузите DXF-файл, чтобы увидеть сцену</p>
              </div>
            )}
          </main>

          <aside className="scrollbar-thin flex w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l border-ink-200/70 bg-ink-50 p-3">
            {scene && (
              <Card className="p-4">
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

            {hoveredZone && (
              <Card className="p-4">
                <h3 className="text-sm font-semibold text-ink-900">{hoveredZone.name}</h3>
                <p className="mt-1 text-sm text-ink-500">{hoveredZone.message}</p>
                <p className="mt-2 text-xs text-ink-400">
                  severity: {hoveredZone.severity}, minDistance: {hoveredZone.minDistance} м
                </p>
              </Card>
            )}

            {selectedObj && (
              <Card className="p-4">
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
              <Card className="p-4 text-xs text-ink-500">
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
        </div>
      </div>

      {/* Правка текстом через ИИ — выпадающее модальное окно, а не постоянная панель */}
      <Dialog open={aiDialogOpen} onOpenChange={setAiDialogOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4.5 w-4.5 text-brand-600" />
              Правка плана текстом
            </DialogTitle>
            <DialogDescription>Опишите на русском, что изменить — планировщик сам посчитает координаты и проверит нормы.</DialogDescription>
          </DialogHeader>
          <Textarea
            rows={4}
            placeholder="Например: посади 5 раскидистых деревьев вдоль южного дома, убери лавки у парковки"
            value={instruction}
            disabled={editing}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                handleTextEdit();
              }
            }}
          />
          <Button className="mt-3 w-full" disabled={editing || !instruction.trim()} onClick={handleTextEdit}>
            {editing ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Модель думает…
              </>
            ) : (
              "Применить (Ctrl/⌘ + Enter)"
            )}
          </Button>
          {editError && <p className="mt-3 rounded-lg bg-danger-500/10 px-3 py-2 text-sm text-danger-500">{editError}</p>}
          {editResult && (
            <div className="mt-3 flex flex-col gap-1.5 rounded-lg bg-ink-50 p-3 text-sm">
              {editResult.explanation && <p className="text-ink-700">{editResult.explanation}</p>}
              <p className="text-xs text-ink-400">
                Применено: {editResult.applied.length}
                {editResult.rejected.length > 0 && `, отклонено: ${editResult.rejected.length}`}
              </p>
              {editResult.rejected.map((r, i) => (
                <p key={`rejected-${i}`} className="text-xs text-danger-500">
                  ✕ {r}
                </p>
              ))}
              {editResult.warnings.map((w, i) => (
                <p key={`warning-${i}`} className="text-xs text-ink-500">
                  ⚠ {w}
                </p>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

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
