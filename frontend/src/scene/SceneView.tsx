import { useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { RestrictionZone, Scene } from "../types";
import { computeSceneBounds } from "../geometry";
import { Ground } from "./Ground";
import { RestrictionZones } from "./RestrictionZones";
import { Buildings } from "./Buildings";
import { PlacedObjects, type TransformMode } from "./PlacedObjects";
import { FitCamera } from "./FitCamera";
import { computeBuildingSetbackZones } from "./buildingSetbacks";
import { Windows, Canopies } from "./FacadeFeatures";

export function SceneView({
  scene,
  selectedId,
  transformMode,
  onSelect,
  onMove,
  onRotate,
  onHoverZone,
}: {
  scene: Scene;
  selectedId: string | null;
  transformMode: TransformMode;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onRotate: (id: string, rotationY: number) => void;
  onHoverZone: (zone: RestrictionZone | null) => void;
}) {
  const bounds = useMemo(() => computeSceneBounds(scene), [scene]);
  const width = bounds.maxX - bounds.minX;
  const depth = bounds.maxZ - bounds.minZ;
  const gridSize = Math.max(width, depth, 20) * 1.3;
  const gridDivisions = Math.min(120, Math.max(10, Math.round(gridSize / 15)));

  // Здания не двигаются при редактировании (перетаскивать можно только
  // деревья/кусты/лавки/фонари), поэтому кольца отступов достаточно
  // пересчитывать только при загрузке новой сцены — держим их на `boundary`,
  // а не на `scene.objects`, иначе каждый drag-move дерева гонял бы offset по
  // всем зданиям заново (в локации 5 их 264).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const buildingSetbackZones = useMemo(() => computeBuildingSetbackZones(scene.objects), [scene.boundary]);
  const displayZones = useMemo(
    () => [...scene.restrictions, ...buildingSetbackZones],
    [scene.restrictions, buildingSetbackZones]
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
      <PlacedObjects
        objects={scene.objects}
        restrictions={scene.restrictions}
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
      <OrbitControls makeDefault />
    </Canvas>
  );
}
