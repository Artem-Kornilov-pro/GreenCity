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
