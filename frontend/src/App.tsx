import { useCallback, useEffect, useMemo, useState } from "react";
import { SceneView } from "./scene/SceneView";
import type { TransformMode } from "./scene/PlacedObjects";
import {
  CATEGORY_LABELS,
  fetchCatalog,
  fetchModelManifest,
  type CatalogCategory,
  type CatalogItem,
} from "./catalog";
import {
  uploadDxf,
  generateGreenery,
  editWithText,
  exportDxf,
  listProjects,
  createProject,
  loadProject,
  saveProject,
  renameProject,
  deleteProject,
  type TextEditResult,
  type ProjectSummary,
} from "./api";
import { loadSession, clearSession, login as apiLogin, register as apiRegister, logout as apiLogout, whoAmI, type Session } from "./auth";
import { checkViolations, computeSceneBounds } from "./geometry";
import { plantKindOfObjectType } from "./setbackNorms";
import type { RestrictionZone, Scene, SceneObject } from "./types";
import "./App.css";

const MAX_PROJECTS = 3; // держим в паре с backend/projects.py::MAX_PROJECTS_PER_USER -- лимит проверяет бэкенд, тут только для подсказки в интерфейсе

function App() {
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

  // Аккаунт: логин/пароль, гостевой режим -- это просто session === null,
  // редактор выше от него не зависит вовсе (см. api.ts/auth.ts).
  const [session, setSession] = useState<Session | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register" | null>(null);
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // Проекты (backend/projects.py) -- до трёх на аккаунт.
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [newProjectName, setNewProjectName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);

  // Каталог -- источник правды по видам посадок (backend/plant_catalog.py).
  // Манифест перечисляет реально имеющиеся .glb; нет манифеста -- рисуем
  // примитивами, это штатный режим до того, как положили модели.
  useEffect(() => {
    fetchCatalog()
      .then((items) => {
        setCatalog(items);
        setSelectedItemId((prev) => prev || items[0]?.id || "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    fetchModelManifest().then(setAvailableModels);
  }, []);

  const refreshProjects = useCallback((session: Session) => {
    listProjects(session)
      .then(setProjects)
      .catch((e) => setProjectError(e instanceof Error ? e.message : String(e)));
  }, []);

  // Сохранённый refresh-токен переживает перезагрузку страницы, но мог
  // истечь за 30 дней или быть отозван (logout в другой вкладке) -- whoAmI
  // проверяет его перед тем, как показать интерфейс как "вошёл", а не
  // просто верит localStorage.
  useEffect(() => {
    const saved = loadSession();
    if (!saved) return;
    whoAmI(saved.refreshToken).then((username) => {
      if (username) {
        setSession(saved);
        refreshProjects(saved);
      } else {
        clearSession();
      }
    });
  }, [refreshProjects]);

  const handleAuthSubmit = useCallback(async () => {
    if (!authMode || !authUsername.trim() || !authPassword) return;
    setAuthBusy(true);
    setAuthError(null);
    try {
      const result = authMode === "login" ? await apiLogin(authUsername.trim(), authPassword) : await apiRegister(authUsername.trim(), authPassword);
      setSession(result);
      refreshProjects(result);
      setAuthMode(null);
      setAuthUsername("");
      setAuthPassword("");
    } catch (e) {
      setAuthError(e instanceof Error ? e.message : String(e));
    } finally {
      setAuthBusy(false);
    }
  }, [authMode, authUsername, authPassword, refreshProjects]);

  const handleLogout = useCallback(() => {
    if (session) apiLogout(session.refreshToken); // отозвать сессию на сервере -- не дожидаемся ответа, локальный выход не должен от него зависеть
    clearSession();
    setSession(null);
    setProjects([]);
    setCurrentProjectId(null);
  }, [session]);

  const handleSaveAsNewProject = useCallback(async () => {
    if (!session || !scene || !newProjectName.trim()) return;
    setProjectBusy(true);
    setProjectError(null);
    try {
      const created = await createProject(session, newProjectName.trim(), scene);
      setNewProjectName("");
      setCurrentProjectId(created.id);
      refreshProjects(session);
    } catch (e) {
      setProjectError(e instanceof Error ? e.message : String(e));
    } finally {
      setProjectBusy(false);
    }
  }, [session, scene, newProjectName, refreshProjects]);

  const handleSaveCurrentProject = useCallback(async () => {
    if (!session || !scene || !currentProjectId) return;
    setProjectBusy(true);
    setProjectError(null);
    try {
      await saveProject(session, currentProjectId, scene);
      refreshProjects(session);
    } catch (e) {
      setProjectError(e instanceof Error ? e.message : String(e));
    } finally {
      setProjectBusy(false);
    }
  }, [session, scene, currentProjectId, refreshProjects]);

  const handleLoadProject = useCallback(
    async (id: string) => {
      if (!session) return;
      setProjectBusy(true);
      setProjectError(null);
      try {
        const project = await loadProject(session, id);
        setScene(project.scene);
        setCurrentProjectId(project.id);
        setSelectedId(null);
      } catch (e) {
        setProjectError(e instanceof Error ? e.message : String(e));
      } finally {
        setProjectBusy(false);
      }
    },
    [session]
  );

  const handleRenameProject = useCallback(
    async (id: string) => {
      if (!session || !renameValue.trim()) return;
      setProjectBusy(true);
      setProjectError(null);
      try {
        await renameProject(session, id, renameValue.trim());
        setRenamingId(null);
        refreshProjects(session);
      } catch (e) {
        setProjectError(e instanceof Error ? e.message : String(e));
      } finally {
        setProjectBusy(false);
      }
    },
    [session, renameValue, refreshProjects]
  );

  const handleDeleteProject = useCallback(
    async (id: string) => {
      if (!session) return;
      setProjectBusy(true);
      setProjectError(null);
      try {
        await deleteProject(session, id);
        if (currentProjectId === id) setCurrentProjectId(null);
        refreshProjects(session);
      } catch (e) {
        setProjectError(e instanceof Error ? e.message : String(e));
      } finally {
        setProjectBusy(false);
      }
    },
    [session, currentProjectId, refreshProjects]
  );

  const handleSelect = useCallback((id: string | null) => {
    setSelectedId(id);
    setTransformMode("translate"); // при выборе нового объекта — всегда начинаем с перемещения
  }, []);

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
    setScene((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        objects: prev.objects.map((o) => (o.id === id ? { ...o, position: { ...o.position, x, z } } : o)),
      };
    });
  }, []);

  const handleAddObject = useCallback(
    (item: CatalogItem) => {
      const type = item.object_type;
      setScene((prev) => {
        if (!prev) return prev;
        const bounds = computeSceneBounds(prev);
        const cx = (bounds.minX + bounds.maxX) / 2;
        const cz = (bounds.minZ + bounds.maxZ) / 2;
        // Небольшой детерминированный разброс, чтобы повторные клики "Добавить"
        // не сыпали новые объекты друг на друга ровно в центре сцены.
        const sameTypeCount = prev.objects.filter((o) => o.type === type).length;
        const offsetX = (sameTypeCount % 5) * 2.5;
        const offsetZ = Math.floor(sameTypeCount / 5) * 2.5;

        const id = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).slice(0, 8);
        const newObject: SceneObject = {
          id: `${type}_manual_${id}`,
          type,
          model: item.model,
          position: { x: cx + offsetX, y: 0, z: cz + offsetZ },
          rotation: 0,
          scale: 1,
          // catalogId -- ключ, по которому рендер и будущий агент понимают,
          // что именно за вид посадки тут стоит.
          metadata: { catalogId: item.id, label: item.label },
        };
        setSelectedId(newObject.id);
        setTransformMode("translate");
        return { ...prev, objects: [...prev.objects, newObject] };
      });
    },
    []
  );

  const handleDelete = useCallback((id: string) => {
    setScene((prev) => {
      if (!prev) return prev;
      return { ...prev, objects: prev.objects.filter((o) => o.id !== id) };
    });
    setSelectedId((prev) => (prev === id ? null : prev));
  }, []);

  const handleRotate = useCallback((id: string, rotationY: number) => {
    setScene((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        objects: prev.objects.map((o) => (o.id === id ? { ...o, rotation: rotationY } : o)),
      };
    });
  }, []);

  const handleGenerateGreenery = useCallback(async () => {
    if (!scene) return;
    setGenerating(true);
    setError(null);
    try {
      const updated = await generateGreenery(scene);
      if (updated) {
        setScene(updated);
      } else {
        // Бэкенд пока заглушка (backend/main.py::generate_greenery) -- null
        // означает "ещё не реализовано", а не ошибку сети. Сцену не трогаем.
        setError("Автогенерация растительности пока не реализована на бэкенде.");
      }
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
      // Модель могла удалить выбранный объект -- не держим выделение на пустоте.
      setSelectedId((prev) => (prev && result.scene.objects.some((o) => o.id === prev) ? prev : null));
    } catch (e) {
      // Ошибку показываем в самой панели правки, а не только в полосе вверху
      // страницы: после минуты ожидания взгляд у пользователя на панели, и
      // уведомление наверху выглядело так, будто "ничего не произошло".
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditing(false);
    }
  }, [scene, instruction]);

  const handleExport = () => {
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

  useEffect(() => {
    if (!selectedId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      // Не удаляем, если фокус в поле ввода (тут единственный input — скрытый
      // file-инпут загрузки DXF, но проверка на будущее не помешает).
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
  // С подключённым паком в каталоге сотни позиций -- без фильтра выпадающий
  // список превращается в бесконечную прокрутку.
  const filteredCatalog = useMemo(() => {
    const q = catalogFilter.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter((i) => i.label.toLowerCase().includes(q));
  }, [catalog, catalogFilter]);

  // Если фильтр отсёк текущий выбор -- берём первую подходящую позицию.
  // Вычисляем при рендере, а не через useEffect с setState: эффект здесь
  // порождал бы лишний цикл рендера ради значения, которое и так выводится
  // из фильтра.
  const effectiveItemId = filteredCatalog.some((i) => i.id === selectedItemId)
    ? selectedItemId
    : (filteredCatalog[0]?.id ?? "");
  const selectedItem = catalogById.get(effectiveItemId);

  const selectedObj = scene?.objects.find((o) => o.id === selectedId) ?? null;
  const selectedViolations = selectedObj
    ? checkViolations(
        selectedObj.position.x,
        selectedObj.position.z,
        scene?.restrictions ?? [],
        plantKindOfObjectType(selectedObj.type)
      )
    : [];

  return (
    <div className="app">
      <header className="topbar">
        <h1>GreenCity — редактор озеленения</h1>
        <label className="upload-btn">
          {loading ? "Загрузка..." : "Загрузить DXF"}
          <input
            type="file"
            accept=".dxf"
            hidden
            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
        </label>
        {scene && (
          <button className="generate-btn" onClick={handleGenerateGreenery} disabled={generating}>
            {generating ? "Генерация..." : "Сгенерировать растительность автоматически"}
          </button>
        )}
        {scene && (
          <button className="export-btn" onClick={handleExport}>
            Экспорт JSON
          </button>
        )}
        {scene && (
          <button className="export-btn" onClick={handleExportDxf} disabled={exportingDxf}>
            {exportingDxf ? "Экспорт..." : "Экспорт DXF"}
          </button>
        )}

        <div className="auth-bar">
          {session ? (
            <>
              <span className="auth-username">{session.username}</span>
              <button className="auth-link" onClick={handleLogout}>
                Выйти
              </button>
            </>
          ) : authMode ? (
            <form
              className="auth-form"
              onSubmit={(e) => {
                e.preventDefault();
                handleAuthSubmit();
              }}
            >
              <input
                placeholder="Имя пользователя"
                value={authUsername}
                disabled={authBusy}
                onChange={(e) => setAuthUsername(e.target.value)}
                autoFocus
              />
              <input
                type="password"
                placeholder="Пароль"
                value={authPassword}
                disabled={authBusy}
                onChange={(e) => setAuthPassword(e.target.value)}
              />
              <button type="submit" disabled={authBusy || !authUsername.trim() || !authPassword}>
                {authBusy ? "..." : authMode === "login" ? "Войти" : "Создать аккаунт"}
              </button>
              <button type="button" className="auth-link" onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}>
                {authMode === "login" ? "Регистрация" : "Уже есть аккаунт"}
              </button>
              <button
                type="button"
                className="auth-link"
                onClick={() => {
                  setAuthMode(null);
                  setAuthError(null);
                }}
              >
                Отмена
              </button>
            </form>
          ) : (
            <>
              <span className="auth-username muted">Гость</span>
              <button className="auth-link" onClick={() => setAuthMode("login")}>
                Войти
              </button>
              <button className="auth-link" onClick={() => setAuthMode("register")}>
                Регистрация
              </button>
            </>
          )}
        </div>
      </header>
      {authError && <div className="error-banner">{authError}</div>}

      {error && <div className="error-banner">{error}</div>}

      <div className="main">
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
          <div className="empty-state">Загрузите DXF-файл, чтобы увидеть сцену</div>
        )}

        <aside className="sidebar">
          {session && (
            <div className="panel projects-panel">
              <strong>Мои проекты ({projects.length}/{MAX_PROJECTS})</strong>
              {projectError && <div className="edit-result rejected-item">✕ {projectError}</div>}
              {projects.length === 0 && <p className="muted hint">Проектов пока нет.</p>}
              {projects.map((p) => (
                <div key={p.id} className={p.id === currentProjectId ? "project-row current" : "project-row"}>
                  {renamingId === p.id ? (
                    <>
                      <input
                        className="rename-input"
                        value={renameValue}
                        autoFocus
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && handleRenameProject(p.id)}
                      />
                      <button disabled={projectBusy} onClick={() => handleRenameProject(p.id)}>
                        ✓
                      </button>
                      <button disabled={projectBusy} onClick={() => setRenamingId(null)}>
                        ✕
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="project-name" title={p.name} onClick={() => handleLoadProject(p.id)}>
                        {p.id === currentProjectId ? "▶ " : ""}
                        {p.name}
                      </span>
                      <button
                        className="icon-btn"
                        title="Переименовать"
                        disabled={projectBusy}
                        onClick={() => {
                          setRenamingId(p.id);
                          setRenameValue(p.name);
                        }}
                      >
                        ✎
                      </button>
                      <button className="icon-btn" title="Удалить" disabled={projectBusy} onClick={() => handleDeleteProject(p.id)}>
                        🗑
                      </button>
                    </>
                  )}
                </div>
              ))}
              {scene && currentProjectId && (
                <button className="apply-btn" disabled={projectBusy} onClick={handleSaveCurrentProject}>
                  Сохранить в «{projects.find((p) => p.id === currentProjectId)?.name}»
                </button>
              )}
              {scene && projects.length < MAX_PROJECTS && (
                <div className="add-row">
                  <input
                    placeholder="Название нового проекта"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSaveAsNewProject()}
                  />
                  <button disabled={projectBusy || !newProjectName.trim()} onClick={handleSaveAsNewProject}>
                    Сохранить как новый
                  </button>
                </div>
              )}
              {scene && projects.length >= MAX_PROJECTS && (
                <p className="muted hint">Достигнут лимит {MAX_PROJECTS} проектов — удалите один, чтобы сохранить новый.</p>
              )}
            </div>
          )}

          {scene && (
            <div className="panel text-edit-panel">
              <strong>Правка текстом (ИИ)</strong>
              <textarea
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
              <button className="apply-btn" disabled={editing || !instruction.trim()} onClick={handleTextEdit}>
                {editing ? "Модель думает..." : "Применить (Ctrl/⌘ + Enter)"}
              </button>
              {editError && <div className="edit-result rejected-item">✕ {editError}</div>}
              {editResult && (
                <div className="edit-result">
                  {editResult.explanation && <div>{editResult.explanation}</div>}
                  <div className="muted">
                    Применено: {editResult.applied.length}
                    {editResult.rejected.length > 0 && `, отклонено: ${editResult.rejected.length}`}
                  </div>
                  {editResult.rejected.map((r, i) => (
                    <div key={`rejected-${i}`} className="rejected-item">
                      ✕ {r}
                    </div>
                  ))}
                  {editResult.warnings.map((w, i) => (
                    <div key={`warning-${i}`} className="warning-item">
                      ⚠ {w}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {scene && (
            <div className="panel add-panel">
              <strong>Добавить объект</strong>
              <input
                className="catalog-filter"
                type="text"
                placeholder="Поиск: раскидистое, низкое, сосна..."
                value={catalogFilter}
                onChange={(e) => setCatalogFilter(e.target.value)}
              />
              <div className="add-row">
                <select value={effectiveItemId} onChange={(e) => setSelectedItemId(e.target.value)}>
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
                <button
                  className="add-btn"
                  disabled={!selectedItem}
                  onClick={() => selectedItem && handleAddObject(selectedItem)}
                >
                  + Добавить
                </button>
              </div>
              <p className="muted hint">
                {catalogFilter ? `${filteredCatalog.length} из ${catalog.length}` : `${catalog.length} видов`} в каталоге
                {availableModels.size > 0
                  ? `, 3D-моделей загружено: ${availableModels.size}`
                  : ", 3D-модели не подключены — рисуются заглушки"}
              </p>
            </div>
          )}

          {hoveredZone && (
            <div className="panel zone-info">
              <strong>{hoveredZone.name}</strong>
              <p>{hoveredZone.message}</p>
              <p className="muted">
                severity: {hoveredZone.severity}, minDistance: {hoveredZone.minDistance} м
              </p>
            </div>
          )}

          {selectedObj && (
            <div className="panel object-info">
              <strong>
                {selectedObj.type} ({selectedObj.id})
              </strong>
              <p className="muted">
                x={selectedObj.position.x.toFixed(2)} z={selectedObj.position.z.toFixed(2)}, поворот=
                {((selectedObj.rotation * 180) / Math.PI).toFixed(0)}°
              </p>
              <div className="mode-toggle">
                <button
                  className={transformMode === "translate" ? "mode-btn active" : "mode-btn"}
                  onClick={() => setTransformMode("translate")}
                >
                  Двигать
                </button>
                <button
                  className={transformMode === "rotate" ? "mode-btn active" : "mode-btn"}
                  onClick={() => setTransformMode("rotate")}
                >
                  Поворачивать
                </button>
              </div>
              {selectedViolations.length > 0 ? (
                <div className="violations">
                  {selectedViolations.map((v) => (
                    <div key={v.zone.id} className="violation-item">
                      ⚠ {v.zone.message} (мин. {v.minDistance} м)
                    </div>
                  ))}
                </div>
              ) : (
                <div className="ok">Нарушений отступов нет</div>
              )}
              <button className="delete-btn" onClick={() => handleDelete(selectedObj.id)}>
                Удалить (Delete)
              </button>
            </div>
          )}

          {scene && (
            <div className="panel legend">
              <div>
                <span className="dot forbidden" /> запрещено
              </div>
              <div>
                <span className="dot warning" /> предупреждение
              </div>
              <div>
                <span className="dot allowed" /> разрешено
              </div>
              <p className="muted hint">
                Клик по объекту — выбрать. "Двигать" — тащить стрелками по земле, "Поворачивать" — вращать
                вокруг своей оси (пригодится для лавки, дорожки, изгороди). Delete/Backspace или кнопка
                "Удалить" — убрать выбранный объект. Дорожки и живую изгородь собирайте из нескольких
                сегментов, ставя и разворачивая их друг за другом.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

export default App;
