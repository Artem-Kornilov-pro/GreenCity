import { useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { Point2, RestrictionZone, Scene } from "../types";
import type { CatalogItem } from "../catalog";
import { computeSceneBounds } from "../geometry";
import { Ground } from "./Ground";
import { RestrictionZones } from "./RestrictionZones";
import { Buildings } from "./Buildings";
import { PlacedObjects, type TransformMode } from "./PlacedObjects";
import { FitCamera } from "./FitCamera";
import { Windows, Canopies } from "./FacadeFeatures";
import { Curbs } from "./CurbStrips";
import { AreaSelectionDraw } from "./AreaSelectionDraw";

export function SceneView({
  scene,
  catalogById,
  availableModels,
  selectedId,
  transformMode,
  selectionMode,
  onSelect,
  onMove,
  onRotate,
  onHoverZone,
  onAreaSelected,
}: {
  scene: Scene;
  catalogById: Map<string, CatalogItem>;
  availableModels: Set<string>;
  selectedId: string | null;
  transformMode: TransformMode;
  selectionMode: boolean;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onRotate: (id: string, rotationY: number) => void;
  onHoverZone: (zone: RestrictionZone | null) => void;
  onAreaSelected: (polygon: Point2[]) => void;
}) {
  const bounds = useMemo(() => computeSceneBounds(scene), [scene]);
  const width = bounds.maxX - bounds.minX;
  const depth = bounds.maxZ - bounds.minZ;
  const gridSize = Math.max(width, depth, 20) * 1.3;
  const gridDivisions = Math.min(120, Math.max(10, Math.round(gridSize / 15)));

  // Кольца отступов приходят готовыми с бэкенда (building_setbacks.py): там
  // буфер считает shapely, который корректно разрешает самопересечения на
  // сложных контурах -- прежний самописный обход вершин на фронте портил их у
  // части домов.
  const displayZones = useMemo(
    () => [...scene.restrictions, ...(scene.buildingSetbacks ?? [])],
    [scene.restrictions, scene.buildingSetbacks]
  );

  return (
    // Логарифмический depth-буфер — без него полигоны/линии, расположенные в
    // одной плоскости, начинают мерцать (z-fighting) на больших дистанциях,
    // а локация 5 — авеню длиной ~4км, обычной точности буфера не хватает.
    <Canvas
      shadows="percentage"
      gl={{ logarithmicDepthBuffer: true }}
      camera={{ position: [40, 45, 40], fov: 45 }}
      onPointerMissed={() => onSelect(null)}
    >
      <color attach="background" args={["#cfe0e8"]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[30, 50, 20]} intensity={1.1} castShadow />
      <FitCamera bounds={bounds} boundary={scene.boundary} />
      <Ground boundary={scene.boundary} />
      <RestrictionZones zones={displayZones} onHover={onHoverZone} />
      <Buildings objects={scene.objects} />
      <Windows quads={scene.windows ?? []} />
      <Canopies quads={scene.canopies ?? []} />
      <Curbs polylines={scene.curbs ?? []} />
      <PlacedObjects
        objects={scene.objects}
        restrictions={scene.restrictions}
        catalogById={catalogById}
        availableModels={availableModels}
        selectedId={selectedId}
        transformMode={transformMode}
        onSelect={onSelect}
        onMove={onMove}
        onRotate={onRotate}
      />
      <gridHelper
        args={[gridSize, gridDivisions, "#8fa6b3", "#b9cdd6"]}
        position={[(bounds.minX + bounds.maxX) / 2, -0.01, (bounds.minZ + bounds.maxZ) / 2]}
      />
      <AreaSelectionDraw active={selectionMode} onComplete={onAreaSelected} />
      {/* В режиме выделения drag должен обводить участок, а не крутить
          камеру -- поэтому OrbitControls на это время выключены. */}
      <OrbitControls makeDefault enabled={!selectionMode} />
    </Canvas>
  );
}
