import { useCallback, useEffect, useState } from "react";
import { SceneView } from "./scene/SceneView";
import type { TransformMode } from "./scene/PlacedObjects";
import { TREE_PRESETS, BUSH_PRESETS, type TreeKind, type BushKind } from "./scene/plantPresets";
import { uploadDxf, generateGreenery } from "./api";
import { checkViolations, computeSceneBounds } from "./geometry";
import { plantKindOfObjectType } from "./setbackNorms";
import type { RestrictionZone, Scene, SceneObject } from "./types";
import "./App.css";

// Дорожка/изгородь — не полилиния, а линейный сегмент (плитка/секция),
// который двигают и разворачивают как любой другой объект, укладывая
// несколько подряд вручную (см. комментарий у EDITABLE_TYPES в types.ts).
const ADDABLE_TYPES: { type: string; label: string; model: string }[] = [
  { type: "bench", label: "Лавка", model: "/models/bench.glb" },
  { type: "lamp", label: "Фонарь", model: "/models/lamp.glb" },
  { type: "trash", label: "Мусорка", model: "/models/trash.glb" },
  { type: "fountain", label: "Фонтан", model: "/models/fountain.glb" },
  { type: "path_segment", label: "Дорожка (сегмент)", model: "/models/path_segment.glb" },
  { type: "hedge_segment", label: "Живая изгородь (сегмент)", model: "/models/hedge_segment.glb" },
];

const TREE_KIND_OPTIONS = Object.entries(TREE_PRESETS) as [TreeKind, { label: string }][];
const BUSH_KIND_OPTIONS = Object.entries(BUSH_PRESETS) as [BushKind, { label: string }][];

function App() {
  const [scene, setScene] = useState<Scene | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [transformMode, setTransformMode] = useState<TransformMode>("translate");
  const [treeKind, setTreeKind] = useState<TreeKind>("medium");
  const [bushKind, setBushKind] = useState<BushKind>("medium");
  const [hoveredZone, setHoveredZone] = useState<RestrictionZone | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    (type: string, model: string, metadata: Record<string, unknown> = {}) => {
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
          model,
          position: { x: cx + offsetX, y: 0, z: cz + offsetZ },
          rotation: 0,
          scale: 1,
          metadata,
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
              <div className="add-row">
                <select value={treeKind} onChange={(e) => setTreeKind(e.target.value as TreeKind)}>
                  {TREE_KIND_OPTIONS.map(([kind, preset]) => (
                    <option key={kind} value={kind}>
                      {preset.label}
                    </option>
                  ))}
                </select>
                <button
                  className="add-btn"
                  onClick={() => handleAddObject("tree", "/models/tree.glb", { treeKind })}
                >
                  + Добавить
                </button>
              </div>
              <div className="add-row">
                <select value={bushKind} onChange={(e) => setBushKind(e.target.value as BushKind)}>
                  {BUSH_KIND_OPTIONS.map(([kind, preset]) => (
                    <option key={kind} value={kind}>
                      {preset.label}
                    </option>
                  ))}
                </select>
                <button
                  className="add-btn"
                  onClick={() => handleAddObject("bush", "/models/bush.glb", { bushKind })}
                >
                  + Добавить
                </button>
              </div>
              <div className="add-buttons">
                {ADDABLE_TYPES.map(({ type, label, model }) => (
                  <button key={type} className="add-btn" onClick={() => handleAddObject(type, model)}>
                    + {label}
                  </button>
                ))}
              </div>
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
