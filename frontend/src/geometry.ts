import type { Point2, RestrictionZone } from "./types";

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

// Точка нарушает зону, если она внутри неё, либо снаружи, но ближе minDistance
// к её границе. Для зон-коридоров (трубы/кабели, где minDistance уже заложен в
// ширину полигона парсером) это консервативно требует дополнительный запас —
// сознательный компромисс ради простоты единой проверки для MVP.
export function checkViolations(px: number, pz: number, zones: RestrictionZone[]): RestrictionZone[] {
  return zones.filter((zone) => {
    if (zone.severity === "allowed" || zone.polygon.length < 3) return false;
    if (pointInPolygon(px, pz, zone.polygon)) return true;
    return distanceToPolygonEdge(px, pz, zone.polygon) < zone.minDistance;
  });
}
