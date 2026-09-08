import { useCallback, useState } from "react";
import { SceneView } from "./scene/SceneView";
import { uploadDxf } from "./api";
import { checkViolations } from "./geometry";
import { plantKindOfObjectType } from "./setbackNorms";
import type { RestrictionZone, Scene } from "./types";
import "./App.css";

function App() {
  const [scene, setScene] = useState<Scene | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredZone, setHoveredZone] = useState<RestrictionZone | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
            onSelect={setSelectedId}
            onMove={handleMove}
            onHoverZone={setHoveredZone}
          />
        ) : (
          <div className="empty-state">Загрузите DXF-файл, чтобы увидеть сцену</div>
        )}

        <aside className="sidebar">
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
                x={selectedObj.position.x.toFixed(2)} z={selectedObj.position.z.toFixed(2)}
              </p>
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
                Клик по дереву/кусту/лавке/фонарю — выбрать и перетащить стрелками.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

export default App;
