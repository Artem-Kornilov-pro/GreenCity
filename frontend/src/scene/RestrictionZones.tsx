import { useMemo } from "react";
import * as THREE from "three";
import { Line } from "@react-three/drei";
import type { RestrictionZone } from "../types";
import { flatPolygonGeometry } from "./geometryHelpers";

const COLOR_BY_SEVERITY: Record<string, string> = {
  forbidden: "#e0433b",
  warning: "#e8a83c",
  allowed: "#3fae5a",
};

// Смещение "к камере" через polygonOffset (а не через накопление крошечного Y
// на каждую зону по индексу) — устойчиво и к 2000+ зонам локации 5, и к любому
// масштабу сцены: чем важнее severity, тем меньше offset (=ближе к камере),
// поэтому forbidden всегда читается поверх warning/allowed, а не тонет в них.
const OFFSET_BY_SEVERITY: Record<string, number> = {
  forbidden: -3,
  warning: -2,
  allowed: -1,
};

// polygonOffset решает z-fighting на общей глубине, но порядок альфа-блендинга
// прозрачных объектов Three.js сортирует отдельно (по renderOrder, при равенстве
// — по дистанции до камеры) — без явного renderOrder две наложенные зоны могли
// в зависимости от угла обзора менять, какая перекрывает какую. Задаём его
// явно тем же приоритетом severity, что и offset выше.
const RENDER_ORDER_BY_SEVERITY: Record<string, number> = {
  allowed: 0,
  warning: 1,
  forbidden: 2,
};

// Реальная (не только полигон-офсетная) высота по severity — без неё все зоны
// лежат ровно на Y=0.01, и raycasting (наведение мышью) не может определить,
// какая из наложенных зон "сверху": polygonOffset — чисто растровый трюк для
// GPU, на пересечение луча с геометрией он не влияет. Из-за этого при наведении
// почти всегда "выигрывала" крупная фоновая зона газона (allowed), а не более
// специфичная forbidden/warning-зона поверх неё. Три уровня высоты достаточно —
// forbidden физически выше warning выше allowed, поэтому луч сначала попадает
// в самую важную зону.
const Y_BY_SEVERITY: Record<string, number> = {
  allowed: 0.01,
  warning: 0.02,
  forbidden: 0.03,
};

export function RestrictionZones({
  zones,
  onHover,
}: {
  zones: RestrictionZone[];
  onHover: (zone: RestrictionZone | null) => void;
}) {
  return (
    <>
      {zones.map((zone) => (
        <ZoneMesh key={zone.id} zone={zone} onHover={onHover} />
      ))}
    </>
  );
}

function ZoneMesh({
  zone,
  onHover,
}: {
  zone: RestrictionZone;
  onHover: (zone: RestrictionZone | null) => void;
}) {
  const geometry = useMemo(() => flatPolygonGeometry(zone.polygon), [zone]);
  const y = Y_BY_SEVERITY[zone.severity] ?? 0.01;
  const outlinePoints = useMemo<[number, number, number][]>(() => {
    if (zone.polygon.length < 3) return [];
    const pts = zone.polygon.map((p): [number, number, number] => [p.x, y + 0.005, p.z]);
    pts.push(pts[0]);
    return pts;
  }, [zone, y]);

  if (!geometry) return null;
  const color = COLOR_BY_SEVERITY[zone.severity] ?? "#999999";
  const offset = OFFSET_BY_SEVERITY[zone.severity] ?? 0;
  const order = RENDER_ORDER_BY_SEVERITY[zone.severity] ?? 0;

  return (
    <group>
      <mesh
        geometry={geometry}
        position={[0, y, 0]}
        renderOrder={order}
        onPointerOver={(e) => {
          e.stopPropagation();
          onHover(zone);
        }}
        onPointerOut={(e) => {
          e.stopPropagation();
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
      {outlinePoints.length > 0 && (
        <Line points={outlinePoints} color={color} lineWidth={1.4} transparent opacity={0.9} renderOrder={order + 10} />
      )}
    </group>
  );
}
