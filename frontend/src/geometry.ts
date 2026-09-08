import type { Point2, RestrictionZone, Scene } from "./types";
import { setbackFor, type PlantKind } from "./setbackNorms";

export function pointInPolygon(px: number, pz: number, poly: Point2[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i].x, zi = poly[i].z;
    const xj = poly[j].x, zj = poly[j].z;
    const intersect =
      zi > pz !== zj > pz && px < ((xj - xi) * (pz - zi)) / (zj - zi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function distanceToSegment(px: number, pz: number, x1: number, z1: number, x2: number, z2: number): number {
  const dx = x2 - x1, dz = z2 - z1;
  const len2 = dx * dx + dz * dz;
  if (len2 < 1e-9) return Math.hypot(px - x1, pz - z1);
  let t = ((px - x1) * dx + (pz - z1) * dz) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), pz - (z1 + t * dz));
}

export function distanceToPolygonEdge(px: number, pz: number, poly: Point2[]): number {
  let min = Infinity;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    min = Math.min(min, distanceToSegment(px, pz, poly[j].x, poly[j].z, poly[i].x, poly[i].z));
  }
  return min;
}

export interface ZoneViolation {
  zone: RestrictionZone;
  minDistance: number; // применённый отступ — из каталога норм для plantKind, либо zone.minDistance
}

// Точка нарушает зону, если она внутри неё, либо снаружи, но ближе применимого
// отступа к её границе. Для деревьев/кустарников отступ берётся из каталога
// норм (setbackNorms.ts) — он различается по виду посадки; для прочих
// объектов (лавка, фонарь) используется общий zone.minDistance. Для
// зон-коридоров (трубы/кабели, где minDistance уже заложен в ширину полигона
// парсером) это консервативно требует дополнительный запас сверху —
// сознательный компромисс ради простоты единой проверки для MVP.
export function checkViolations(
  px: number,
  pz: number,
  zones: RestrictionZone[],
  plantKind?: PlantKind
): ZoneViolation[] {
  const result: ZoneViolation[] = [];
  for (const zone of zones) {
    if (zone.severity === "allowed" || zone.polygon.length < 3) continue;
    const minDistance = plantKind ? setbackFor(zone.type, plantKind, zone.minDistance) : zone.minDistance;
    if (pointInPolygon(px, pz, zone.polygon) || distanceToPolygonEdge(px, pz, zone.polygon) < minDistance) {
      result.push({ zone, minDistance });
    }
  }
  return result;
}

export interface SceneBounds {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  maxHeight: number;
}

const FALLBACK_BOUNDS: SceneBounds = { minX: -20, maxX: 20, minZ: -20, maxZ: 20, maxHeight: 10 };

// Границы вычисляются из boundary (не меняется при перетаскивании объектов),
// поэтому камеру можно безопасно перепозиционировать на каждую загрузку сцены,
// не дёргая её при каждом drag-move.
export function computeSceneBounds(scene: Scene): SceneBounds {
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity, maxHeight = 10;
  const consider = (x: number, z: number) => {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
  };

  scene.boundary?.polygon.forEach((p) => consider(p.x, p.z));
  if (!Number.isFinite(minX)) {
    scene.objects.forEach((o) => consider(o.position.x, o.position.z));
    scene.restrictions.forEach((r) => r.polygon.forEach((p) => consider(p.x, p.z)));
  }
  scene.objects.forEach((o) => {
    const h = o.metadata?.height as number | undefined;
    if (typeof h === "number") maxHeight = Math.max(maxHeight, h);
  });

  if (!Number.isFinite(minX)) return FALLBACK_BOUNDS;
  return { minX, maxX, minZ, maxZ, maxHeight };
}
