import * as THREE from "three";
import type { Point2 } from "../types";

// Общий рецепт для плоских/выдавленных полигонов участка: строим THREE.Shape в
// плоскости XY из (x, -z), затем rotateX(-90°) переводит его в мировые
// координаты (x, depth, z) — depth=0 для плоской заливки, depth=height для
// выдавленного здания. Без этого разворота ось выдавливания (Z шейпа) не
// совпадает с "вверх" (Y) сцены.
export function flatPolygonGeometry(points: Point2[]): THREE.BufferGeometry | null {
  if (points.length < 3) return null;
  const shape = new THREE.Shape(points.map((p) => new THREE.Vector2(p.x, -p.z)));
  const geo = new THREE.ShapeGeometry(shape);
  geo.rotateX(-Math.PI / 2);
  return geo;
}

export function extrudedPolygonGeometry(points: Point2[], height: number): THREE.BufferGeometry | null {
  if (points.length < 3) return null;
  const shape = new THREE.Shape(points.map((p) => new THREE.Vector2(p.x, -p.z)));
  const geo = new THREE.ExtrudeGeometry(shape, { depth: Math.max(height, 0.1), bevelEnabled: false });
  geo.rotateX(-Math.PI / 2);
  return geo;
}

// Плоские слои участка -- сетка, земля, газон, зоны -- рисуются строго по
// порядку (renderOrder) и НЕ пишут глубину: сетка (-4), земля (-3), газон
// (-2), зоны (прозрачные, после всего непрозрачного). Сравнивать их глубину
// между собой видеокарте не приходится вовсе. Раньше их разводили только по
// высоте (сантиметры), и на крупной сцене из DWG, когда камера отходит на
// километры, точности буфера глубины не хватало -- на части видеокарт он
// 16-битный, и тогда не спасали даже 20 см: земля и сетка мерцали
// (Берзарина). Здания, деревья и бордюры пишут глубину как обычно и
// рисуются поверх. Высоты оставлены разнесёнными -- на случай, если порядок
// когда-нибудь поменяется.
export const FLAT_LAYER_ORDER = { grid: -4, ground: -3, lawn: -2 } as const;
