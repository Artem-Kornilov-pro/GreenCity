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
import { uploadDxf, generateGreenery } from "./api";
import { checkViolations, computeSceneBounds } from "./geometry";
import { plantKindOfObjectType } from "./setbackNorms";
import type { RestrictionZone, Scene, SceneObject } from "./types";
import "./App.css";

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
  const [error, setError] = useState<string | null>(null);

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
      </header>

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
