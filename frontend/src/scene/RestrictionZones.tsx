import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { RestrictionZone } from "../types";
import { flatPolygonGeometry } from "./geometryHelpers";

const COLOR_BY_SEVERITY: Record<string, string> = {
  forbidden: "#e0433b",
  warning: "#e8a83c",
  allowed: "#3fae5a",
};

// Больше стольких зон в одной группе severity контур не рисуется (см.
// комментарий у <lineSegments> ниже).
const DENSE_GROUP_OUTLINE_CUTOFF = 30;

// Смещение к камере через polygonOffset: чем важнее severity, тем ближе, --
// forbidden всегда читается поверх warning и allowed при любом числе зон.
const OFFSET_BY_SEVERITY: Record<string, number> = {
  forbidden: -3,
  warning: -2,
  allowed: -1,
};

// Порядок прозрачных объектов three.js сортирует отдельно, по renderOrder;
// без него наложенные зоны меняли бы порядок в зависимости от угла обзора.
const RENDER_ORDER_BY_SEVERITY: Record<string, number> = {
  allowed: 0,
  warning: 1,
  forbidden: 2,
};

// Высота по severity: polygonOffset не влияет на raycast, и без разной высоты
// наведение мышью попадало бы в фоновый газон, а не в запретную зону над ним.
// Порядок и высоты плоских слоёв -- FLAT_LAYER_ORDER в geometryHelpers.ts.
const Y_BY_SEVERITY: Record<string, number> = {
  allowed: 0.04,
  warning: 0.07,
  forbidden: 0.1,
};

const SEVERITIES = ["allowed", "warning", "forbidden"] as const;

interface MergedGroup {
  severity: string;
  fill: THREE.BufferGeometry;
  outline: THREE.BufferGeometry;
  // Треугольники в слитой геометрии идут подряд по зонам: зона под курсором
  // находится двоичным поиском по faceIndex.
  zoneEnds: number[];
  zones: RestrictionZone[];
}

// Зоны одной severity сливаются в одну геометрию: три меша и три набора линий
// на всю сцену, сколько бы ни было зон.
function buildGroups(zones: RestrictionZone[]): MergedGroup[] {
  const groups: MergedGroup[] = [];

  for (const severity of SEVERITIES) {
    const list = zones.filter((z) => z.severity === severity && z.polygon.length >= 3);
    if (!list.length) continue;

    const fillPositions: number[] = [];
    const outlinePositions: number[] = [];
    const zoneEnds: number[] = [];
    const kept: RestrictionZone[] = [];

    for (const zone of list) {
      const geo = flatPolygonGeometry(zone.polygon);
      if (!geo) continue;
      // ShapeGeometry индексированная: toNonIndexed раскладывает вершины в
      // порядке треугольников, и их можно дописывать в общий буфер.
      const flat = geo.index ? geo.toNonIndexed() : geo;
      const pos = flat.getAttribute("position");
      for (let i = 0; i < pos.count; i++) {
        fillPositions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      }
      // Временные геометрии нужны только как триангулятор -- держать их в
      // памяти после копирования вершин незачем.
      if (flat !== geo) flat.dispose();
      geo.dispose();

      const poly = zone.polygon;
      for (let i = 0; i < poly.length; i++) {
        const a = poly[i];
        const b = poly[(i + 1) % poly.length];
        outlinePositions.push(a.x, 0.005, a.z, b.x, 0.005, b.z);
      }

      zoneEnds.push(fillPositions.length / 9);
      kept.push(zone);
    }

    if (!kept.length) continue;

    const fill = new THREE.BufferGeometry();
    fill.setAttribute("position", new THREE.Float32BufferAttribute(fillPositions, 3));
    const outline = new THREE.BufferGeometry();
    outline.setAttribute("position", new THREE.Float32BufferAttribute(outlinePositions, 3));

    groups.push({ severity, fill, outline, zoneEnds, zones: kept });
  }

  return groups;
}

function zoneAtFace(group: MergedGroup, faceIndex: number | null | undefined): RestrictionZone | null {
  if (faceIndex === undefined || faceIndex === null) return null;
  let lo = 0;
  let hi = group.zoneEnds.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (faceIndex < group.zoneEnds[mid]) hi = mid;
    else lo = mid + 1;
  }
  return group.zones[lo] ?? null;
}

export function RestrictionZones({
  zones,
  onHover,
}: {
  zones: RestrictionZone[];
  onHover: (zone: RestrictionZone | null) => void;
}) {
  const groups = useMemo(() => buildGroups(zones), [zones]);

  // onPointerMove срабатывает на каждый пиксель движения: сообщаем о зоне,
  // только если она сменилась, иначе страница перерисовывалась бы постоянно.
  const lastHoveredIdRef = useRef<string | null>(null);

  // Слитые геометрии живут в памяти GPU, и React сам их не освобождает: без
  // явного dispose каждая загрузка новой сцены оставляла бы предыдущую висеть.
  useEffect(
    () => () => {
      for (const g of groups) {
        g.fill.dispose();
        g.outline.dispose();
      }
    },
    [groups]
  );

  return (
    <>
      {groups.map((group) => {
        const color = COLOR_BY_SEVERITY[group.severity] ?? "#999999";
        const offset = OFFSET_BY_SEVERITY[group.severity] ?? 0;
        const order = RENDER_ORDER_BY_SEVERITY[group.severity] ?? 0;
        const y = Y_BY_SEVERITY[group.severity] ?? 0.04;
        return (
          <group key={group.severity} position={[0, y, 0]}>
            <mesh
              geometry={group.fill}
              renderOrder={order}
              onPointerMove={(e) => {
                e.stopPropagation();
                const zone = zoneAtFace(group, e.faceIndex);
                const id = zone?.id ?? null;
                if (id === lastHoveredIdRef.current) return;
                lastHoveredIdRef.current = id;
                onHover(zone);
              }}
              onPointerOut={(e) => {
                e.stopPropagation();
                if (lastHoveredIdRef.current === null) return;
                lastHoveredIdRef.current = null;
                onHover(null);
              }}
            >
              <meshBasicMaterial
                color={color}
                transparent
                opacity={0.28}
                side={THREE.DoubleSide}
                depthWrite={false}
                polygonOffset
                polygonOffsetFactor={offset}
                polygonOffsetUnits={offset}
              />
            </mesh>
            {/* Обычный LineSegments, а не <Line> из drei: тот на каждый
                контур строит отдельную геометрию. На плотных группах контур не
                рисуется совсем: сотни совпадающих полупрозрачных линий на общих
                границах складываются в штриховку. Зону и так видно по заливке. */}
            {group.zones.length <= DENSE_GROUP_OUTLINE_CUTOFF && (
              <lineSegments geometry={group.outline} renderOrder={order + 10}>
                <lineBasicMaterial color={color} transparent opacity={0.9} />
              </lineSegments>
            )}
          </group>
        );
      })}
    </>
  );
}
