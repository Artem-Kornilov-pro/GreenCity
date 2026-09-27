import { memo, Suspense, useMemo, useRef, type RefObject } from "react";
import * as THREE from "three";
import { Html, TransformControls } from "@react-three/drei";
import type { RestrictionZone, SceneObject } from "../types";
import type { CatalogItem } from "../catalog";
import { objectDisplayName, resolveCatalogItem } from "../catalog";
import { buildZoneIndex, violatesAt } from "../geometry";
import { plantKindOfObjectType } from "../setbackNorms";
import { ObjectVisual } from "./ObjectVisual";
import { InstancedVegetationGroup, type InstancePlacement } from "./InstancedVegetation";

export type TransformMode = "translate" | "rotate";

export function PlacedObjects({
  objects,
  restrictions,
  catalogById,
  availableModels,
  selectedId,
  transformMode,
  onSelect,
  onMove,
  onRotate,
}: {
  objects: SceneObject[];
  restrictions: RestrictionZone[];
  catalogById: Map<string, CatalogItem>;
  availableModels: Set<string>;
  selectedId: string | null;
  transformMode: TransformMode;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onRotate: (id: string, rotationY: number) => void;
}) {
  // Всё, что не зависит от выделения, считается один раз на изменение сцены, а
  // не на каждый рендер: выделение объекта меняет selectedId, и без этого
  // мемо весь список (включая проверку нарушений по всем зонам и разбор
  // каталога) пересчитывался бы на каждый клик.
  const rows = useMemo(() => {
    const index = buildZoneIndex(restrictions);
    return objects
      .filter((o) => o.type !== "building")
      .map((obj) => {
        // Подъезд встроен в стену по построению — отступ от здания к нему
        // неприменим, проверять и подсвечивать это как нарушение бессмысленно.
        // Мощение (path_segment) по норме допускается поверх охранной зоны
        // сети — лёгкая дорожка не мешает обслуживанию труб, в отличие от
        // капитальной постройки или посадки с корнями (см. backend
        // courtyard_design.py, где новая дорожка намеренно не исключает такие
        // зоны). Подсвечивать её там как нарушение — визуальное вранье:
        // дорожке там и правда можно быть.
        const exemptFromViolations = obj.type === "entrance" || obj.type === "path_segment";
        const item = resolveCatalogItem(obj, catalogById, availableModels);
        return {
          obj,
          item,
          hasModel: item ? availableModels.has(item.model) : false,
          violated:
            !exemptFromViolations &&
            violatesAt(
              obj.position.x,
              obj.position.z,
              index,
              plantKindOfObjectType(obj.type),
              typeof obj.metadata.species === "string" ? obj.metadata.species : undefined
            ),
        };
      });
  }, [objects, restrictions, catalogById, availableModels]);

  // Разбивка на "рисуется поштучно" и "рисуется инстансированно" (perf
  // follow-up) -- зависит от selectedId (выделенный объект ВСЕГДА рисуется
  // поштучно, ему нужен TransformControls), поэтому отдельный useMemo от
  // rows выше: пересобрать группировку на каждый клик дёшево (просто
  // распределение по Map), а не пересчитывать нарушения по всем зонам
  // заново, как предотвращает memo у rows.
  const { individualRows, instancedGroups } = useMemo(() => {
    const individualRows: typeof rows = [];
    const groups = new Map<string, InstancePlacement[]>();
    for (const row of rows) {
      const { obj, item, hasModel, violated } = row;
      // Инстансируем только то, что реально рисуется .glb-моделью как есть:
      // нарушения (см. ObjectVisual) и объекты без модели рисуются
      // процедурными примитивами Three.js-геометрией, которые тут не строим
      // батчами -- их обычно на порядки меньше, чем деревьев/кустов.
      if (hasModel && !violated && item && obj.id !== selectedId) {
        const list = groups.get(item.model);
        const placement: InstancePlacement = {
          id: obj.id,
          position: [obj.position.x, obj.position.y, obj.position.z],
          rotation: obj.rotation,
          scale: obj.scale,
        };
        if (list) list.push(placement);
        else groups.set(item.model, [placement]);
      } else {
        individualRows.push(row);
      }
    }
    return { individualRows, instancedGroups: groups };
  }, [rows, selectedId]);

  return (
    <>
      {individualRows.map(({ obj, item, hasModel, violated }) => (
        <PlacedObjectItem
          key={obj.id}
          obj={obj}
          item={item}
          hasModel={hasModel}
          violated={violated}
          selected={obj.id === selectedId}
          label={obj.id === selectedId ? objectDisplayName(obj, catalogById) : undefined}
          transformMode={transformMode}
          onSelect={onSelect}
          onMove={onMove}
          onRotate={onRotate}
        />
      ))}
      <Suspense fallback={null}>
        {Array.from(instancedGroups.entries()).map(([url, placements]) => (
          <InstancedVegetationGroup key={url} url={url} placements={placements} onSelect={onSelect} />
        ))}
      </Suspense>
    </>
  );
}

// memo обязателен, а не "на всякий случай": без него смена selectedId
// перерисовывает каждый объект сцены, а каждая такая перерисовка у объекта с
// моделью означала новый клон графа glTF (см. GltfModel). Колбэки приходят из
// EditorPage через useCallback, объект строки -- из useMemo выше, поэтому
// сравнение пропсов действительно отсекает лишнее, а не проходит впустую.
const PlacedObjectItem = memo(function PlacedObjectItem({
  obj,
  item,
  hasModel,
  violated,
  selected,
  label,
  transformMode,
  onSelect,
  onMove,
  onRotate,
}: {
  obj: SceneObject;
  item?: CatalogItem;
  hasModel: boolean;
  violated: boolean;
  selected: boolean;
  // Вид выбранного объекта -- подпись над ним, едет вместе с ним при
  // перетаскивании (группа двигается TransformControls).
  label?: string;
  transformMode: TransformMode;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, z: number) => void;
  onRotate: (id: string, rotationY: number) => void;
}) {
  const groupRef = useRef<THREE.Group>(null);
  // Редактируется всё, что есть в каталоге: пользователь сам это поставил или
  // может поставить. Структурные объекты из подосновы (подъезды, площадки) в
  // каталог не входят и не двигаются.
  const editable = item !== undefined;

  const body = (
    <group
      ref={groupRef}
      position={[obj.position.x, obj.position.y, obj.position.z]}
      rotation={[0, obj.rotation, 0]}
      scale={obj.scale}
      onClick={(e) => {
        e.stopPropagation();
        if (editable) onSelect(obj.id);
      }}
    >
      <ObjectVisual type={obj.type} item={item} hasModel={hasModel} violated={violated} />
      {label && (
        // zIndexRange ниже боковой панели (z-20) и выдвижных (z-30): иначе
        // подпись drei по умолчанию рисуется поверх всего интерфейса.
        <Html position={[0, (item?.dimensions.height ?? 1.5) + 0.8, 0]} center zIndexRange={[15, 10]} style={{ pointerEvents: "none" }}>
          <div className="whitespace-nowrap rounded-lg border border-ink-200/70 bg-white/95 px-2.5 py-1 text-xs font-semibold text-ink-900 shadow-soft">
            {label}
          </div>
        </Html>
      )}
    </group>
  );

  if (!selected || !editable) return body;

  // Двигать — только по земле (Y выключен). Поворачивать — только вокруг
  // вертикальной оси (X/Z выключены), иначе объект завалится набок.
  const isTranslate = transformMode === "translate";

  return (
    <TransformControls
      object={groupRef as RefObject<THREE.Object3D>}
      mode={transformMode}
      showX={isTranslate}
      showY={!isTranslate}
      showZ={isTranslate}
      onMouseUp={() => {
        const g = groupRef.current;
        if (!g) return;
        if (isTranslate) {
          onMove(obj.id, g.position.x, g.position.z);
        } else {
          onRotate(obj.id, g.rotation.y);
        }
      }}
    >
      {body}
    </TransformControls>
  );
});
