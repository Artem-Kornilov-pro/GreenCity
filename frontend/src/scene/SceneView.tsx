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
import { Lawns } from "./Lawns";
import { AreaSelectionDraw } from "./AreaSelectionDraw";

// Высота сетки-ориентира: под землёй (Ground, -0.10), см. комментарий у gridHelper.
const GRID_Y = -0.3;

export function SceneView({
  scene,
  sceneLoadToken,
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
  sceneLoadToken: number;
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
    //
    // powerPreference: "high-performance" -- на ноутбуках с двумя GPU
    // (встроенная Intel + дискретная NVIDIA/AMD, issue #55 follow-up:
    // реальный тест на i5 11-го поколения + RTX 3050 Ti) браузер по
    // умолчанию часто выбирает встроенную GPU ради экономии энергии, даже
    // когда страница просит WebGL2. Это ПОДСКАЗКА браузеру/драйверу, не
    // гарантия -- на Windows финальный выбор GPU для процесса браузера
    // всё ещё может быть переопределён в настройках Windows ("Параметры
    // графики" -> выбрать браузер -> "Высокая производительность") или в
    // панели NVIDIA (Управление 3D-настройками -> Настройки программы ->
    // добавить браузер -> "Высокопроизводительный процессor NVIDIA") --
    // без этого шага на части систем подсказка страницы игнорируется.
    <Canvas
      shadows="percentage"
      gl={{ logarithmicDepthBuffer: true, powerPreference: "high-performance" }}
      camera={{ position: [40, 45, 40], fov: 45 }}
      onPointerMissed={() => onSelect(null)}
    >
      <color attach="background" args={["#cfe0e8"]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[30, 50, 20]} intensity={1.1} castShadow />
      <FitCamera bounds={bounds} sceneLoadToken={sceneLoadToken} />
      <Ground boundary={scene.boundary} />
      <Lawns lawns={scene.lawns ?? []} />
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
      {/* Сетка -- заметно ниже земли (Ground, -0.10), а не в сантиметре над
          ней: у земли, газона и зон вершины лежат в буфере мировыми
          координатами, и у сцен из DWG вдали от начала координат во float32
          они "дрожат" на миллиметры-сантиметры при каждом движении камеры.
          Сантиметрового зазора не хватало -- линии сетки мерцали сквозь
          землю (Берзарина). Над участком сетку закрывает земля, вокруг --
          она видна как ориентир. */}
      <gridHelper
        args={[gridSize, gridDivisions, "#8fa6b3", "#b9cdd6"]}
        position={[(bounds.minX + bounds.maxX) / 2, GRID_Y, (bounds.minZ + bounds.maxZ) / 2]}
      />
      <AreaSelectionDraw active={selectionMode} onComplete={onAreaSelected} />
      {/* В режиме выделения drag должен обводить участок, а не крутить
          камеру -- поэтому OrbitControls на это время выключены. */}
      <OrbitControls makeDefault enabled={!selectionMode} />
    </Canvas>
  );
}
