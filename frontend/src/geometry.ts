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

// Наибольший отступ среди табличных норм и правил по породе (MAX_SETBACK_M
// из setbackNorms.ts: липа -- 10 м от здания, больше базовых 5 м).
// setbackFor для зоны без табличного значения возвращает её собственный
// minDistance, поэтому реально применённый отступ никогда не превышает
// max(zone.minDistance, MAX_SETBACK_M) -- на столько и нужно расширять
// габарит зоны в индексе, чтобы не потерять нарушение у точки за её границей
// (иначе липа дальше 5 м, но ближе положенных ей 10 м, могла бы пройти мимо
// индекса как "не нарушает").

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

// Проверка "нарушает ли точка зону" в лоб -- это перебор всех зон на каждый
// объект: на реальном файле из 20 улиц (1625 зон) и сцене в пару тысяч посадок
// выходит под 200 млн операций, причём на каждый рендер. Сеточный индекс
// сводит это к нескольким зонам в ячейке точки.
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

  // Ячейка по медианному размеру зоны: у коридоров сетей габариты на порядки
  // разные (короткий отвод против магистрали через весь квартал), и среднее
  // тут увело бы сетку в крупную клетку из-за нескольких гигантов.
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

// Возвращает только факт нарушения: вызывающему коду (подсветка объекта в
// сцене) нужен именно он, а собирать список зон на каждый из тысяч объектов --
// лишние аллокации. Разбор, ЧЕМ именно нарушено, делается через checkViolations
// для одного выделенного объекта.
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

// То же самое, что checkViolations, но по уже построенному ZoneIndex --
// нужна для панели выделенного объекта в EditorPage.tsx: там на КАЖДЫЙ
// ре-рендер страницы (включая ре-рендеры от наведения мыши на другую зону,
// не связанные с самим выделенным объектом) раньше заново перебирались все
// зоны сцены линейно. violatesAt() рядом даёт только факт нарушения (булево)
// -- этого достаточно для подсветки объекта в PlacedObjects.tsx, но здесь
// нужен список конкретных нарушенных зон с их message/severity для панели.
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
