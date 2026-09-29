import type { Point2, RestrictionZone, Scene } from "./types";
import { MAX_SETBACK_M, setbackFor, type PlantKind } from "./setbackNorms";

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

// Точка нарушает зону, если она внутри неё или ближе отступа к её границе.
// Для деревьев и кустарников отступ -- из норм (setbackNorms.ts), для прочих
// объектов -- zone.minDistance. Для коридоров сетей это даёт запас сверх
// нормы -- осознанное упрощение.
export function checkViolations(
  px: number,
  pz: number,
  zones: RestrictionZone[],
  plantKind?: PlantKind,
  species?: string
): ZoneViolation[] {
  const result: ZoneViolation[] = [];
  for (const zone of zones) {
    if (zone.severity === "allowed" || zone.polygon.length < 3) continue;
    const minDistance = plantKind ? setbackFor(zone.type, plantKind, zone.minDistance, species) : zone.minDistance;
    if (pointInPolygon(px, pz, zone.polygon) || distanceToPolygonEdge(px, pz, zone.polygon) < minDistance) {
      result.push({ zone, minDistance });
    }
  }
  return result;
}

// Наибольший возможный отступ (липа -- 10 м от здания): на столько
// расширяется габарит зоны в индексе, чтобы не пропустить нарушение у точки
// за её границей.

// Сетка не длиннее этого по стороне: 256x256 ячеек -- потолок памяти индекса,
// дальше выгоднее проверять чуть больше зон в ячейке, чем держать сетку.
const MAX_GRID_SIDE = 256;

interface IndexedZone {
  zone: RestrictionZone;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

export interface ZoneIndex {
  originX: number;
  originZ: number;
  cell: number;
  cols: number;
  rows: number;
  buckets: IndexedZone[][];
}

// Сеточный индекс зон: вместо перебора всех зон на каждый объект -- несколько
// зон в ячейке точки.
export function buildZoneIndex(zones: RestrictionZone[]): ZoneIndex {
  const items: IndexedZone[] = [];
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;

  for (const zone of zones) {
    // Те же отсечки, что и в checkViolations: allowed ничего не запрещает,
    // вырожденный полигон нечем нарушать.
    if (zone.severity === "allowed" || zone.polygon.length < 3) continue;
    let zMinX = Infinity, zMaxX = -Infinity, zMinZ = Infinity, zMaxZ = -Infinity;
    for (const p of zone.polygon) {
      if (p.x < zMinX) zMinX = p.x;
      if (p.x > zMaxX) zMaxX = p.x;
      if (p.z < zMinZ) zMinZ = p.z;
      if (p.z > zMaxZ) zMaxZ = p.z;
    }
    const pad = Math.max(zone.minDistance, MAX_SETBACK_M);
    const item = { zone, minX: zMinX - pad, maxX: zMaxX + pad, minZ: zMinZ - pad, maxZ: zMaxZ + pad };
    items.push(item);
    if (item.minX < minX) minX = item.minX;
    if (item.maxX > maxX) maxX = item.maxX;
    if (item.minZ < minZ) minZ = item.minZ;
    if (item.maxZ > maxZ) maxZ = item.maxZ;
  }

  if (!items.length || !Number.isFinite(minX)) {
    return { originX: 0, originZ: 0, cell: 1, cols: 0, rows: 0, buckets: [] };
  }

  // Ячейка -- по медианному размеру зоны: среднее увели бы в крупную клетку
  // несколько гигантских коридоров сетей.
  const spans = items.map((i) => Math.max(i.maxX - i.minX, i.maxZ - i.minZ)).sort((a, b) => a - b);
  const median = spans[Math.floor(spans.length / 2)] || 1;
  const width = maxX - minX;
  const depth = maxZ - minZ;
  const cell = Math.max(median, width / MAX_GRID_SIDE, depth / MAX_GRID_SIDE, 0.5);
  const cols = Math.max(1, Math.ceil(width / cell));
  const rows = Math.max(1, Math.ceil(depth / cell));

  const buckets: IndexedZone[][] = Array.from({ length: cols * rows }, () => []);
  for (const item of items) {
    const c0 = Math.max(0, Math.floor((item.minX - minX) / cell));
    const c1 = Math.min(cols - 1, Math.floor((item.maxX - minX) / cell));
    const r0 = Math.max(0, Math.floor((item.minZ - minZ) / cell));
    const r1 = Math.min(rows - 1, Math.floor((item.maxZ - minZ) / cell));
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) buckets[r * cols + c].push(item);
    }
  }

  return { originX: minX, originZ: minZ, cell, cols, rows, buckets };
}

// Только факт нарушения -- для подсветки тысяч объектов без лишних
// аллокаций. Список нарушенных зон -- checkViolationsAt.
export function violatesAt(
  px: number,
  pz: number,
  index: ZoneIndex,
  plantKind?: PlantKind,
  species?: string
): boolean {
  if (!index.cols) return false;
  const c = Math.floor((px - index.originX) / index.cell);
  const r = Math.floor((pz - index.originZ) / index.cell);
  if (c < 0 || r < 0 || c >= index.cols || r >= index.rows) return false;

  for (const item of index.buckets[r * index.cols + c]) {
    if (px < item.minX || px > item.maxX || pz < item.minZ || pz > item.maxZ) continue;
    const zone = item.zone;
    const minDistance = plantKind ? setbackFor(zone.type, plantKind, zone.minDistance, species) : zone.minDistance;
    if (pointInPolygon(px, pz, zone.polygon) || distanceToPolygonEdge(px, pz, zone.polygon) < minDistance) {
      return true;
    }
  }
  return false;
}

// checkViolations по готовому ZoneIndex -- список нарушенных зон для панели
// выделенного объекта.
export function checkViolationsAt(
  px: number,
  pz: number,
  index: ZoneIndex,
  plantKind?: PlantKind,
  species?: string
): ZoneViolation[] {
  if (!index.cols) return [];
  const c = Math.floor((px - index.originX) / index.cell);
  const r = Math.floor((pz - index.originZ) / index.cell);
  if (c < 0 || r < 0 || c >= index.cols || r >= index.rows) return [];

  const result: ZoneViolation[] = [];
  for (const item of index.buckets[r * index.cols + c]) {
    if (px < item.minX || px > item.maxX || pz < item.minZ || pz > item.maxZ) continue;
    const zone = item.zone;
    const minDistance = plantKind ? setbackFor(zone.type, plantKind, zone.minDistance, species) : zone.minDistance;
    if (pointInPolygon(px, pz, zone.polygon) || distanceToPolygonEdge(px, pz, zone.polygon) < minDistance) {
      result.push({ zone, minDistance });
    }
  }
  return result;
}

// Земля окрестностей (scene/Ground.tsx) -- только под тем, что есть в чертеже,
// без заливки пустого места: площадка вокруг каждого дома (оболочка контура с
// запасом pad) и полотна улиц и дорожек. Дома у границы и улицы между ними и
// участком не висят в пустоте, а сцена не расширяется.
const STREET_ZONE_TYPES = new Set(["road", "pedestrian_path"]);

export function computeSurroundings(scene: Scene, pad: number): { yards: Point2[][]; streets: Point2[][] } {
  const yards: Point2[][] = [];
  for (const o of scene.objects) {
    if (o.type !== "building") continue;
    const footprint = (o.metadata.footprint as Point2[] | undefined) ?? [];
    const pts: Point2[] = [];
    // Запас -- восемь точек вокруг вершины: оболочка отстоит от неё на ~pad.
    for (const p of footprint) {
      for (let i = 0; i < 8; i++) {
        const a = (i * Math.PI) / 4;
        pts.push({ x: p.x + pad * Math.cos(a), z: p.z + pad * Math.sin(a) });
      }
    }
    const hull = convexHull(pts);
    if (hull.length >= 3) yards.push(hull);
  }
  const streets = scene.restrictions.filter((r) => STREET_ZONE_TYPES.has(r.type)).map((r) => r.polygon);
  return { yards, streets };
}

// Выпуклая оболочка (монотонная цепочка Эндрю).
function convexHull(points: Point2[]): Point2[] {
  if (points.length < 3) return [];
  const sorted = [...points].sort((a, b) => a.x - b.x || a.z - b.z);
  const cross = (o: Point2, a: Point2, b: Point2) => (a.x - o.x) * (b.z - o.z) - (a.z - o.z) * (b.x - o.x);
  const half = (pts: Point2[]) => {
    const out: Point2[] = [];
    for (const p of pts) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...half(sorted), ...half(sorted.reverse())];
}

export interface SceneBounds {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  maxHeight: number;
}

const FALLBACK_BOUNDS: SceneBounds = { minX: -20, maxX: 20, minZ: -20, maxZ: 20, maxHeight: 10 };

// Границы -- по boundary: он не меняется при перетаскивании объектов.
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
