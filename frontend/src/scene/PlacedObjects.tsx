import { memo, Suspense, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Html, TransformControls } from "@react-three/drei";
import { createPortal, useThree } from "@react-three/fiber";
import type { RestrictionZone, SceneObject } from "../types";
import type { CatalogItem } from "../catalog";
import { fitHeight, objectDisplayName, resolveCatalogItem } from "../catalog";
import { buildZoneIndex, isOffsite, violatesAt } from "../geometry";
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
  // Всё, что не зависит от выделения, считается один раз на изменение сцены,
  // а не на каждый клик.
  const rows = useMemo(() => {
    const index = buildZoneIndex(restrictions);
    // Объекты из чертежа за границей участка не рисуются: это невидимые
    // ограничения для расстановки, а не объекты участка.
    return objects
      .filter((o) => o.type !== "building" && !isOffsite(o))
      .map((obj) => {
        // Подъезд встроен в стену -- отступ от здания к нему неприменим.
        // Мощение по норме допускается над охранной зоной сети -- это не
        // нарушение.
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
              typeof obj.metadata.species === "string" ? obj.metadata.species : undefined,
              Array.isArray(obj.metadata.rootBarrier) && obj.metadata.rootBarrier.length > 0,
            ),
        };
      });
  }, [objects, restrictions, catalogById, availableModels]);

  // Поштучно или инстансированно -- зависит от выделения (выделенному
  // объекту нужен TransformControls), поэтому отдельный useMemo: перераскладка
  // дешёвая, нарушения заново не считаются.
  const { individualRows, instancedGroups } = useMemo(() => {
    const individualRows: typeof rows = [];
    const groups = new Map<string, InstancePlacement[]>();
    for (const row of rows) {
      const { obj, item, hasModel, violated } = row;
      // Инстансируются только объекты, которые рисуются .glb-моделью как есть;
      // нарушения и объекты без модели рисуются примитивами поштучно.
      if (hasModel && !violated && item && obj.id !== selectedId) {
        const list = groups.get(item.model);
        const placement: InstancePlacement = {
          id: obj.id,
          position: [obj.position.x, obj.position.y, obj.position.z],
          rotation: obj.rotation,
          scale: obj.scale,
          height: fitHeight(item),
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

// memo обязателен: без него смена выделения перерисовывала бы каждый объект,
// а у объекта с моделью это новый клон графа glTF.
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
  const scene = useThree((s) => s.scene);
  // Двойник объекта в мировых координатах -- им управляет гизмо (см. ниже).
  const proxy = useMemo(() => new THREE.Object3D(), []);
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

  // Гизмо -- в корне сцены и тянет двойник объекта в мировых координатах, а
  // не сам объект внутри отражённой группы SceneView (SCENE_TO_WORLD, z -> -z).
  // Внутри отражения гизмо оказывалось в зеркальной точке и ехало в обратную
  // сторону, а поворот шёл против движения мыши. Мир -> сцена: z -> -z,
  // поворот -> -поворот (отражение меняет направление вращения).
  return (
    <>
      {body}
      <GizmoProxySync proxy={proxy} obj={obj} />
      {createPortal(
        <>
          <primitive object={proxy} />
          <TransformControls
            object={proxy}
            mode={transformMode}
            showX={isTranslate}
            showY={!isTranslate}
            showZ={isTranslate}
            onObjectChange={() => {
              const g = groupRef.current;
              if (!g) return;
              g.position.set(proxy.position.x, proxy.position.y, -proxy.position.z);
              g.rotation.y = -yawOf(proxy);
            }}
            onMouseUp={() => {
              if (isTranslate) {
                onMove(obj.id, proxy.position.x, -proxy.position.z);
              } else {
                onRotate(obj.id, -yawOf(proxy));
              }
            }}
          />
        </>,
        scene,
      )}
    </>
  );
});

// Поворот вокруг вертикали по направлению оси X объекта: rotation.y у three.js
// после поворота больше чем на 90° «заворачивается» (Euler XYZ: x = z = π).
const AXIS_X = new THREE.Vector3(1, 0, 0);
function yawOf(o: THREE.Object3D): number {
  const v = AXIS_X.clone().applyQuaternion(o.quaternion);
  return Math.atan2(-v.z, v.x);
}

// Двойник встаёт туда, где объект в мире, -- при выделении и после каждой
// правки сцены (перемещение, поворот, отмена).
function GizmoProxySync({ proxy, obj }: { proxy: THREE.Object3D; obj: SceneObject }) {
  const { x, y, z } = obj.position;
  const rotation = obj.rotation;
  useLayoutEffect(() => {
    proxy.position.set(x, y, -z);
    proxy.rotation.set(0, -rotation, 0);
    proxy.updateMatrixWorld();
  }, [proxy, x, y, z, rotation]);
  return null;
}
